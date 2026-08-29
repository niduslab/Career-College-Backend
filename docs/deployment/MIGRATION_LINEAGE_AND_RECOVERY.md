# Migration Lineage and Recovery

How the migration history fractured across three machines, why CI/CD broke, and how to bring a local clone onto the branch's lineage without losing dev data.

## 1. State of play

| | migration files tracked | `.gitignore` ignores migrations | `deploy.yml` | `pr-checks.yml` |
|---|---|---|---|---|
| `master` (`bf0be59`) | yes — 10 files | no, removed | `makemigrations --check --dry-run` | bare `makemigrations` |
| `main` (`d0f3bf1`) | no | yes | n/a (deploy gates `master`) | bare `makemigrations` |
| `feature/ai/course_outline_generator` (`38b0b60`) | no | yes | pre-fix | bare `makemigrations` |

`main` is a strict ancestor of `master` — fast-forward is possible, the branches have not diverged.

Lineage on `master`, 10 files across 8 apps:

```
admin_console    0001_initial, 0002_initial
authentication   0001_initial
courses          0001_initial, 0002_learningpath_learningpathenrollment_and_more
id_verification  0001_initial
messaging        0001_initial
notifications    0001_initial
payments         0001_initial
webinars         0001_initial
```

Lineage on disk in a long-lived local clone, 61 files, none of them tracked:

```
admin_console 2   authentication 4   courses 31   id_verification 3
messaging 2       notifications 12   payments 4   webinars 3
```

`analytics`, `core` and `realtime` own no models and have no migrations.

## 2. Root cause

Two settings combined to keep schema history out of git entirely:

- `.gitignore` carried `**/migrations/*` with a `!**/migrations/__init__.py` negation. Migration files were never committed.
- `deploy.yml` ran `python manage.py makemigrations --noinput` on the EC2 box before `migrate`, under `set -euo pipefail`.

So the schema history lived on whichever machine happened to run `makemigrations` first, and three machines ended up with three different answers: this clone accumulated an incremental lineage across ~40 feature branches, the server generated a single fresh `0001_initial` per app against an empty prod DB at first deploy, and a throwaway clone later generated a third set that became what is on `master` today.

Why the first deploy worked and later ones did not: `makemigrations --noinput` exits non-zero the moment Django needs to ask a question — a non-nullable field with no default, an ambiguous rename — and `pipefail` turns that into a dead deploy. A second contributor was stdin: the remote script arrived over `ssh 'bash -s' <<'REMOTE'`, and `docker compose run` without `-T` competed for the same stdin.

`git reset --hard origin/master` (deploy.yml) does not delete untracked or ignored files, so every migration file a machine generated for itself survived every subsequent deploy.

Committing the migration files to `master` fixed the deploy because the server's `makemigrations` then had nothing left to generate.

## 3. Why merging `master` into an old clone breaks it

Git treats ignored files as expendable. Merging a `master` that *tracks* `courses/migrations/0001_initial.py` overwrites the ignored local file at that path with no warning and no conflict. Files that exist only locally are left exactly where they are, because git does not know about them.

Applied to the numbers above:

- 8 files overwritten — the `0001_initial.py` of each app.
- 2 files added with no local counterpart — `admin_console/0002_initial.py`, `courses/0002_learningpath_…py`.
- **53 files survive as orphans**, every one of them declaring a dependency on the `0001_initial` that was just replaced with different content.

`admin_console` then holds both `0002_adminactionlog.py` and `0002_initial.py`, and Django refuses to do anything: `Conflicting migrations detected; multiple leaf nodes in the migration graph`. Elsewhere it surfaces as `NodeNotFoundError` or `InconsistentMigrationHistory`.

There is a second, quieter failure underneath. The local `django_migrations` table records the old lineage, and Django keys applied migrations on `(app, name)`. Both lineages use the name `courses.0001_initial`. Master's initial is therefore treated as already applied and skipped — no error, just divergence. A migration generated on that mixed tree and pushed can then break prod by re-creating a column the server already has.

## 4. Recovery runbook

Brings a local clone onto `master`'s lineage. **The dev database is never dropped and its data is never dumped and reloaded** — only the `django_migrations` ledger is rewritten, and only after a schema diff proves both lineages build the same tables.

Dropping the database, or `DROP SCHEMA public CASCADE`, both discard the dev data, and the second takes `pg_trgm` with it. Reloading afterwards means `pg_dump --data-only` with foreign-key ordering problems and primary-key collisions on `django_content_type` and `auth_permission`, which `migrate` repopulates on its own. The ledger rewrite avoids all of it.

**1. Back up twice.** Copy every `*/migrations/0*.py` to a directory outside the repo, and take `pg_dump -Fc -d <dev_db> -f dev_backup.dump`. Insurance only — nothing below restores from it.

**2. Delete every local migration file except `__init__.py`, in all 8 apps.**

```powershell
Get-ChildItem -Path . -Recurse -Directory -Filter migrations |
  Where-Object { $_.FullName -notmatch '\\venv\\|\\.venv\\' } |
  ForEach-Object { Get-ChildItem $_.FullName -Filter "0*.py" | Remove-Item }
```

**3. Merge `master`.** `git fetch origin master; git merge origin/master`. Master's lineage lands on an empty directory, so no mixing is possible. Confirm afterwards that each app holds only the files listed in §1.

**4. Prove schema parity.** Create a scratch database, run the branch's lineage into it, and diff it against the dev database:

```bash
createdb career_college_schemacheck
psql -d career_college_schemacheck -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
DB_NAME=career_college_schemacheck python manage.py migrate

pg_dump --schema-only --no-owner --no-privileges -d career_college_schemacheck > new.sql
pg_dump --schema-only --no-owner --no-privileges -d <dev_db>                    > old.sql
diff old.sql new.sql
```

Expect no difference — both lineages derive from the same models and end at the same place (local `courses/0031_learningpath…` and master's `courses/0002_learningpath…` are the same change under different numbers). Index names match too: local `authentication/0004` builds `idx_user_email_trgm` / `idx_user_fullname_trgm` with `AddIndexConcurrently`, master's `authentication/0001_initial` builds the same names with plain `AddIndex` after a `TrigramExtension()` — different operation, identical resulting schema.

A raw `diff` of the two dumps will show a lot of noise: an incremental lineage appends columns in the order they were added, a squashed initial declares them in model order, so hundreds of lines differ by position alone. Compare the catalogs instead, sorted, which is order-insensitive:

```sql
-- run against both DBs, sort, compare
select table_name||'|'||column_name||'|'||data_type||'|'||coalesce(character_maximum_length::text,'-')
       ||'|'||is_nullable||'|'||coalesce(column_default,'-')
  from information_schema.columns where table_schema='public' order by 1;
select indexdef from pg_indexes where schemaname='public' order by 1;
select conrelid::regclass::text||'|'||contype::text||'|'||conname||'|'||pg_get_constraintdef(oid)
  from pg_constraint where connamespace='public'::regnamespace order by 1;
```

- No difference → step 5.
- Difference in an identifier only, same definition on both sides → rename it and re-compare. Postgres keeps an auto-generated index or constraint name through a field rename, while a fresh `makemigrations` names it from the current field, so this is the expected shape of a small diff. `ALTER INDEX … RENAME TO …` and `ALTER TABLE … RENAME CONSTRAINT … TO …` are metadata-only.
- Difference in an actual column, type, nullability, or index definition → stop and report it. The lineages genuinely disagree, and the fallback is a rebuild with a `pg_dump --data-only --exclude-table=django_migrations` reload.

Drop the scratch database when done.

**5. Rewrite the ledger, nothing else.** Reset the whole table, not just the eight project apps:

```sql
DELETE FROM django_migrations;
```

```bash
python manage.py migrate --fake
```

`--fake` writes rows and touches no table. Every row of dev data survives.

Deleting only the eight project apps does **not** work. `account.0001_initial`, `socialaccount`, `admin` and `token_blacklist` all depend on `AUTH_USER_MODEL`, so once `authentication`'s rows are gone Django's `check_consistent_history` aborts before it does anything:

```
InconsistentMigrationHistory: Migration account.0001_initial is applied
before its dependency authentication.0001_initial on database 'default'
```

Clearing the table entirely lets `migrate --fake` re-record every app in correct dependency order. Faking the third-party apps is accurate — their tables are already present and their migration files never changed. `post_migrate` still fires and is idempotent, so content types and permissions are left alone.

**6. Verify.**

```bash
python manage.py showmigrations          # every node [X], no unknown node
python manage.py makemigrations --check --dry-run   # "No changes detected"
python manage.py check
python manage.py test                    # 892/892
```

`makemigrations --check` failing here means real drift between master's models and master's migrations — generate it deliberately and read `python manage.py sqlmigrate <app> <name>` before committing. The test run is independent proof that the lineage applies cleanly from an empty database, since the test runner builds one.

Spot-check row counts on `courses_niduscourse`, `authentication_user` and `courses_enrollment` against their pre-change values to confirm the data is untouched.

### For `feature/ai/course_outline_generator` specifically

The feature commit (`38b0b60`) touches 16 files and not one of them is a model — `ai_views.py`, `ai_serializers.py`, `ai_outline_service.py`, `test_ai_outline.py`, `urls.py`, `settings.py`, `.env.example`, docs. The endpoint persists nothing by design. **The merge needs no migration at all, and the PR diff must contain zero migration files.** A migration showing up there is the signal that steps 2–4 went wrong.

Fast-forward `main` to `master` before opening the PR. `main` is a strict ancestor, so this is a content-free operation, and it stops the next branch cut from `main` inheriting the old `.gitignore` and losing its migrations again. Then PR the feature into `main`, which keeps the review diff at the 16 feature files instead of 16 plus all of master's catch-up.

### Run record — `feature/ai/course_outline_generator`, 29 Aug 2026

Executed against `career_college_db` (Postgres 18.2, Python 3.14.2, Django 5.2.14). 61 files and a 410 KB `pg_dump -Fc` backed up first; 61 files deleted; `git merge origin/master` clean, leaving exactly the 10 tracked files.

The dev ledger turned out to hold **34 `courses` rows against 31 files** — `0002_enrollment`, `0018_courseschedule_and_more` and `0019_remove_coursecategory_display_order` were recorded as applied with no file behind them. The schema comparison showed they left no residue.

Schema parity after the scratch-DB build: **658 columns, 469 indexes, 869 constraints — 0 differences**, once three identifiers were renamed. All three came from `Lecture.content_type` having been renamed to `lecture_type`:

```sql
ALTER INDEX lectures_content_type_64772528      RENAME TO lectures_lecture_type_720c5ff7;
ALTER INDEX lectures_content_type_64772528_like RENAME TO lectures_lecture_type_720c5ff7_like;
ALTER TABLE lectures RENAME CONSTRAINT lectures_content_type_not_null TO lectures_lecture_type_not_null;
```

Prod does not need this — the server DB was built by a fresh `makemigrations` and already carries the new names. Only long-lived clones have the old ones.

Result: ledger down to master's 10 nodes, all `[X]`; `makemigrations --check` clean; `check` clean; **892/892 tests pass**; every row count unchanged (22 users, 11 courses, 7 enrollments, 16 sections, 15 lectures, 3 quizzes, 7 orders, 2 webinars). The PR diff carries the 16 feature files and no migration.

## 5. Already fixed, and still open

Fixed on `master`:

- `.gitignore` no longer ignores migrations; the lineage is tracked.
- `deploy.yml` runs `makemigrations --check --dry-run` instead of `makemigrations --noinput` — the server verifies a migration exists, it never authors one.
- The remote script is `scp`'d and run with `ssh -n`, and one-off containers use `docker compose run -T … < /dev/null`, so nothing competes for stdin.

Still open:

- **`pr-checks.yml` line 80 runs bare `makemigrations`.** A PR that changes a model without committing a migration passes CI, because CI generates the file itself and throws it away. It then fails at deploy against the `--check` guard. Change it to `makemigrations --check --dry-run` so the PR fails instead — earlier, and in front of the person who can fix it.
- **`main` still ignores migrations and has none tracked.** Any branch cut from `main` today reproduces the original bug. Fast-forward it to `master`.
- **Stale untracked migration files may remain on EC2.** `reset --hard` never removed them, and now that `.gitignore` no longer covers them they are ordinary untracked files. Verify before assuming they are gone:

  ```bash
  cd /opt/niduscareer/backend
  git status --porcelain -- '*/migrations/*'
  sudo docker compose run --rm --no-deps -T api python manage.py showmigrations < /dev/null
  sudo docker compose run --rm --no-deps -T api python manage.py makemigrations --check --dry-run < /dev/null
  ```

  Take an RDS snapshot before deleting anything. If `showmigrations` and `--check` are both clean, there is nothing to do.

## 6. Rules going forward

- Migrations are source code. Commit them in the same commit as the model change that produced them.
- Never run `makemigrations` on a server or in CI. `--check --dry-run` is a guard; bare `makemigrations` is authoring, and authoring belongs on a developer's machine where the question Django asks can be answered.
- Never hand-edit a migration that has been applied anywhere.
- `--fake` only with a written reason and a schema diff behind it.
- Read `sqlmigrate` output before pushing a migration that touches a populated table.
