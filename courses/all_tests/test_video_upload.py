"""Direct-to-S3 multipart upload endpoints.

The bytes never pass through Django, so every guarantee here rests on what the
server can verify after the fact: that the object key is its own, and that the
finished object is the size the client declared.
"""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.contrib.contenttypes.models import ContentType
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from authentication.models import User
from courses.models import (
    CourseSection,
    Lecture,
    NidusCourse,
    SectionContent,
    VideoAsset,
)

S3_SETTINGS = {
    'AWS_STORAGE_BUCKET_NAME': 'test-bucket',
    'AWS_LOCATION': 'media',
}


def _client_error(code):
    return ClientError({'Error': {'Code': code, 'Message': code}}, 'CompleteMultipartUpload')


@override_settings(**S3_SETTINGS)
class VideoUploadAPITests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            email='vu_instructor@example.com',
            password='pw12345!',
            full_name='VU Instructor',
            user_type='instructor',
            is_email_verified=True,
        )
        cls.course = NidusCourse.objects.create(
            created_by=cls.instructor,
            title='Uploadable Course',
            slug='uploadable-course',
            description='A course used by video-upload tests.',
            status=NidusCourse.CourseStatus.DRAFT,
        )
        cls.course.instructors.add(cls.instructor)
        cls.section = CourseSection.objects.create(
            course=cls.course, title='Section One', position=1
        )
        cls.lecture = Lecture.objects.create(
            section=cls.section,
            title='Video Lecture',
            lecture_type=Lecture.LectureType.VIDEO,
        )
        SectionContent.objects.create(
            section=cls.section,
            item_type=SectionContent.ItemType.LECTURE,
            content_type=ContentType.objects.get_for_model(Lecture),
            object_id=cls.lecture.pk,
            position=1,
        )

    def setUp(self):
        self.client.force_authenticate(user=self.instructor)

    def _initiate(self, s3, file_size=1024):
        s3.create_multipart_upload.return_value = {'UploadId': 'upload-1'}
        return self.client.post(
            reverse('courses:lecture-video-initiate-upload', kwargs={'lecture_id': self.lecture.id}),
            {'filename': 'lesson.mp4', 'content_type': 'video/mp4', 'file_size': file_size},
            format='json',
        )

    def _asset_with_key(self, key='courses/uploadable-course/lectures/1/raw/abc.mp4'):
        return VideoAsset.objects.create(
            lecture=self.lecture,
            video_file=key,
            original_filename='lesson.mp4',
            mime_type='video/mp4',
            file_size=1024,
            is_active=False,
            status=VideoAsset.Status.UPLOADING,
        )

    # -------------------------------------------------------------------------
    # The object key is the server's, not the client's
    # -------------------------------------------------------------------------

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_initiate_stamps_the_object_key_on_the_asset(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3

        response = self._initiate(s3)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        asset = VideoAsset.objects.get(pk=response.data['data']['videoAssetId'])
        # Stored at initiate so later steps never have to trust the client.
        self.assertEqual(asset.video_file.name, response.data['data']['objectKey'])
        self.assertTrue(asset.video_file.name.startswith('courses/uploadable-course/lectures/'))
        self.assertFalse(asset.is_active)

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_part_url_ignores_a_client_supplied_object_key(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3
        s3.generate_presigned_url.return_value = 'https://s3.example/signed'
        asset = self._asset_with_key()

        response = self.client.post(
            reverse('courses:lecture-video-part-url', kwargs={'video_asset_id': asset.id}),
            {'uploadId': 'upload-1', 'partNumber': 1, 'objectKey': 'static/app.js'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        signed_key = s3.generate_presigned_url.call_args.kwargs['Params']['Key']
        self.assertEqual(signed_key, f'media/{asset.video_file.name}')
        self.assertNotIn('static/app.js', signed_key)

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_complete_ignores_a_client_supplied_object_key(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3
        s3.head_object.return_value = {'ContentLength': 1024}
        asset = self._asset_with_key()

        response = self.client.post(
            reverse('courses:lecture-video-complete-upload', kwargs={'video_asset_id': asset.id}),
            {
                'uploadId': 'upload-1',
                'objectKey': 'courses/someone-elses/lectures/9/raw/theirs.mp4',
                'parts': [{'partNumber': 1, 'etag': '"abc"'}],
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        completed_key = s3.complete_multipart_upload.call_args.kwargs['Key']
        self.assertEqual(completed_key, f'media/{asset.video_file.name}')
        asset.refresh_from_db()
        self.assertEqual(asset.video_file.name, 'courses/uploadable-course/lectures/1/raw/abc.mp4')
        self.assertEqual(asset.status, VideoAsset.Status.PROCESSING)
        self.assertTrue(asset.is_active)

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_complete_without_an_initiated_upload_is_422(self, s3_factory):
        s3_factory.return_value = MagicMock()
        asset = self._asset_with_key(key='')

        response = self.client.post(
            reverse('courses:lecture-video-complete-upload', kwargs={'video_asset_id': asset.id}),
            {'uploadId': 'upload-1', 'parts': [{'partNumber': 1, 'etag': '"abc"'}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # -------------------------------------------------------------------------
    # Declared size is enforced against what actually landed
    # -------------------------------------------------------------------------

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_complete_rejects_an_object_larger_than_declared(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3
        # Declared 1 KB at initiate, uploaded 50 GB.
        s3.head_object.return_value = {'ContentLength': 50 * 1024 ** 3}
        asset = self._asset_with_key()

        response = self.client.post(
            reverse('courses:lecture-video-complete-upload', kwargs={'video_asset_id': asset.id}),
            {'uploadId': 'upload-1', 'parts': [{'partNumber': 1, 'etag': '"abc"'}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        s3.delete_object.assert_called_once()
        asset.refresh_from_db()
        self.assertEqual(asset.status, VideoAsset.Status.FAILED)
        self.assertFalse(asset.is_active)

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_complete_accepts_when_head_object_is_unavailable(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3
        # A transient HEAD failure must not discard a genuine upload.
        s3.head_object.side_effect = Exception('boom')
        asset = self._asset_with_key()

        response = self.client.post(
            reverse('courses:lecture-video-complete-upload', kwargs={'video_asset_id': asset.id}),
            {'uploadId': 'upload-1', 'parts': [{'partNumber': 1, 'etag': '"abc"'}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        asset.refresh_from_db()
        self.assertEqual(asset.status, VideoAsset.Status.PROCESSING)

    # -------------------------------------------------------------------------
    # A dead upload session is the client's problem, not a 500
    # -------------------------------------------------------------------------

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_stale_upload_id_is_422_not_500(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3
        s3.complete_multipart_upload.side_effect = _client_error('NoSuchUpload')
        asset = self._asset_with_key()

        response = self.client.post(
            reverse('courses:lecture-video-complete-upload', kwargs={'video_asset_id': asset.id}),
            {'uploadId': 'stale', 'parts': [{'partNumber': 1, 'etag': '"abc"'}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        asset.refresh_from_db()
        self.assertEqual(asset.status, VideoAsset.Status.FAILED)

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_unrecognised_s3_error_is_still_500(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3
        s3.complete_multipart_upload.side_effect = _client_error('InternalError')
        asset = self._asset_with_key()

        response = self.client.post(
            reverse('courses:lecture-video-complete-upload', kwargs={'video_asset_id': asset.id}),
            {'uploadId': 'upload-1', 'parts': [{'partNumber': 1, 'etag': '"abc"'}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    # -------------------------------------------------------------------------
    # Logging — the only server-side record that the bytes landed
    # -------------------------------------------------------------------------

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_completion_is_logged_with_the_asset_id(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3
        s3.head_object.return_value = {'ContentLength': 1024}
        asset = self._asset_with_key()

        with self.assertLogs('courses.all_views.video_upload_views', level='INFO') as logs:
            response = self.client.post(
                reverse('courses:lecture-video-complete-upload', kwargs={'video_asset_id': asset.id}),
                {'uploadId': 'upload-1', 'parts': [{'partNumber': 1, 'etag': '"abc"'}]},
                format='json',
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line = next(m for m in logs.output if 'video-upload completed' in m)
        self.assertIn(f'asset={asset.id}', line)
        self.assertIn('verified=True', line)
        self.assertIn('parts=1', line)

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_unverified_completion_says_so_in_the_log(self, s3_factory):
        s3 = MagicMock()
        s3_factory.return_value = s3
        s3.head_object.side_effect = Exception('boom')
        asset = self._asset_with_key()

        with self.assertLogs('courses.all_views.video_upload_views', level='INFO') as logs:
            self.client.post(
                reverse('courses:lecture-video-complete-upload', kwargs={'video_asset_id': asset.id}),
                {'uploadId': 'upload-1', 'parts': [{'partNumber': 1, 'etag': '"abc"'}]},
                format='json',
            )

        line = next(m for m in logs.output if 'video-upload completed' in m)
        self.assertIn('verified=False', line)

    # -------------------------------------------------------------------------
    # Ownership still gates every step
    # -------------------------------------------------------------------------

    @patch('courses.all_views.video_upload_views._s3_client')
    def test_another_instructor_gets_404_on_part_url(self, s3_factory):
        s3_factory.return_value = MagicMock()
        asset = self._asset_with_key()
        stranger = User.objects.create_user(
            email='vu_stranger@example.com',
            password='pw12345!',
            full_name='VU Stranger',
            user_type='instructor',
            is_email_verified=True,
        )
        self.client.force_authenticate(user=stranger)

        response = self.client.post(
            reverse('courses:lecture-video-part-url', kwargs={'video_asset_id': asset.id}),
            {'uploadId': 'upload-1', 'partNumber': 1},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
