# 🚀 NidusJob API - Error Response Format Guide for Frontend

## IMPORTANT: All API Errors Now Use RFC 7807 Standard Format

**Effective Date:** March 30, 2026  
**Format Standard:** RFC 7807 (IETF Problem Details)  
**Backward Compatible:** YES (via `_legacy` wrapper)

---

## Table of Contents
1. [Standard Response Format](#standard-response-format)
2. [Field Descriptions](#field-descriptions)
3. [HTTP Status Codes](#http-status-codes)
4. [Error Types (Full List)](#error-types-full-list)
5. [Common Error Scenarios](#common-error-scenarios)
6. [Frontend Implementation Examples](#frontend-implementation-examples)
7. [Field-Level Validation Errors](#field-level-validation-errors)
8. [Trace ID for Debugging](#trace-id-for-debugging)
9. [Backward Compatibility (_legacy)](#backward-compatibility-legacy)

---

## Standard Response Format

### Full Error Response Structure

```json
{
  "type": "https://api.nidusjob.com/errors/{error-type}",
  "title": "Human Readable Error Title",
  "status": 400,
  "detail": "Detailed explanation of what went wrong",
  "instance": "/api/endpoint/path/",
  "errors": {
    "field_name": [
      "Error message for this field"
    ]
  },
  "trace_id": "58d32fbb-4d2f-448d-8fa6-2b90eadc04a9",
  "_legacy": {
    "success": false,
    "error": "error_type",
    "message": "Same as detail field",
    "status_code": 400,
    "details": "Same as detail field",
    "errors": {}
  }
}
```

---

## Field Descriptions

| Field | Type | Always Present | Description |
|-------|------|---|---|
| `type` | String | ✅ YES | Error type URI (machine-readable identifier) |
| `title` | String | ✅ YES | Human-readable error category |
| `status` | Number | ✅ YES | HTTP status code (400, 401, 403, 404, 422, 429, 500, 503, 504) |
| `detail` | String | ✅ YES | Detailed explanation of the error |
| `instance` | String | ✅ YES | Request path where error occurred |
| `errors` | Object | ❌ NO* | Field-level validation errors (only for 400 validation errors) |
| `trace_id` | String | ❌ NO* | Unique identifier for debugging (only for 500+ server errors) |
| `_legacy` | Object | ✅ YES | Backward compatibility wrapper (old format fields) |

\* *Only present in specific error scenarios*

---

### Field Value Reference

#### `type` Field Examples
```
https://api.nidusjob.com/errors/validation-error
https://api.nidusjob.com/errors/authentication-error
https://api.nidusjob.com/errors/permission-error
https://api.nidusjob.com/errors/not-found-error
https://api.nidusjob.com/errors/business-logic-error
https://api.nidusjob.com/errors/rate-limit-error
https://api.nidusjob.com/errors/service-unavailable-error
https://api.nidusjob.com/errors/timeout-error
https://api.nidusjob.com/errors/internal-server-error
```

#### `title` Field Examples
```
"Validation Error"
"Authentication Error"
"Permission Denied"
"Not Found"
"Business Logic Error"
"Rate Limit Exceeded"
"Service Unavailable"
"Timeout"
"Internal Server Error"
```

---

## HTTP Status Codes

| Status | Error Type | Meaning | Action |
|--------|-----------|---------|--------|
| **400** | `validation-error` | Invalid request (field errors) | Fix and retry |
| **401** | `authentication-error` | Not authenticated / token expired | Re-login |
| **403** | `permission-error` | Authenticated but not authorized | Ask admin access |
| **404** | `not-found-error` | Resource not found | Check URL/ID |
| **422** | `business-logic-error` | Violation of business rules | Fix business violation |
| **429** | `rate-limit-error` | Too many requests | Wait and retry |
| **500** | `internal-server-error` | Server error (use trace_id for support) | Contact support with trace_id |
| **503** | `service-unavailable-error` | External service down | Retry later |
| **504** | `timeout-error` | Operation timed out | Retry with smaller request |

---

## Error Types (Full List)

### 1. **validation-error** (400)
```json
{
  "type": "https://api.nidusjob.com/errors/validation-error",
  "title": "Validation Error",
  "status": 400,
  "detail": "The request body has validation errors",
  "instance": "/api/jobs/create/",
  "errors": {
    "title": ["Title must be between 10-200 characters."],
    "salary_min": ["Salary must be a positive number."]
  }
}
```

### 2. **authentication-error** (401)
```json
{
  "type": "https://api.nidusjob.com/errors/authentication-error",
  "title": "Authentication Error",
  "status": 401,
  "detail": "Invalid credentials or token expired",
  "instance": "/api/auth/login/"
}
```

### 3. **permission-error** (403)
```json
{
  "type": "https://api.nidusjob.com/errors/permission-error",
  "title": "Permission Denied",
  "status": 403,
  "detail": "You do not have permission to perform this action",
  "instance": "/api/jobs/123/edit/"
}
```

### 4. **not-found-error** (404)
```json
{
  "type": "https://api.nidusjob.com/errors/not-found-error",
  "title": "Not Found",
  "status": 404,
  "detail": "Job with ID 999 not found",
  "instance": "/api/jobs/999/"
}
```

### 5. **business-logic-error** (422)
```json
{
  "type": "https://api.nidusjob.com/errors/business-logic-error",
  "title": "Business Logic Error",
  "status": 422,
  "detail": "Insufficient credits to create job post. Required: 5, Available: 2",
  "instance": "/api/jobs/create/"
}
```

### 6. **rate-limit-error** (429)
```json
{
  "type": "https://api.nidusjob.com/errors/rate-limit-error",
  "title": "Rate Limit Exceeded",
  "status": 429,
  "detail": "Too many requests. Maximum 100 requests per hour allowed",
  "instance": "/api/jobs/search/"
}
```

### 7. **service-unavailable-error** (503)
```json
{
  "type": "https://api.nidusjob.com/errors/service-unavailable-error",
  "title": "Service Unavailable",
  "status": 503,
  "detail": "AI service is temporarily unavailable",
  "instance": "/api/ai/generate-job/"
}
```

### 8. **timeout-error** (504)
```json
{
  "type": "https://api.nidusjob.com/errors/timeout-error",
  "title": "Timeout",
  "status": 504,
  "detail": "Request timeout after 30 seconds",
  "instance": "/api/ai/search/"
}
```

### 9. **internal-server-error** (500)
```json
{
  "type": "https://api.nidusjob.com/errors/internal-server-error",
  "title": "Internal Server Error",
  "status": 500,
  "detail": "An unexpected error occurred during processing",
  "instance": "/api/jobs/create/",
  "trace_id": "58d32fbb-4d2f-448d-8fa6-2b90eadc04a9"
}
```

---

## Common Error Scenarios

### Scenario 1: User Tries to Create Job Without Credits

**Request:**
```javascript
POST /api/jobs/create/
{
  "title": "Senior Developer",
  "description": "..."
}
```

**Response (422):**
```json
{
  "type": "https://api.nidusjob.com/errors/business-logic-error",
  "title": "Business Logic Error",
  "status": 422,
  "detail": "Insufficient credits to create job post. Required: 5, Available: 2",
  "instance": "/api/jobs/create/",
  "_legacy": {
    "success": false,
    "error": "business_logic_error",
    "message": "Insufficient credits to create job post. Required: 5, Available: 2",
    "status_code": 422,
    "details": "Insufficient credits to create job post. Required: 5, Available: 2"
  }
}
```

**Frontend Action:**
```javascript
if (response.status === 422 && response.data.type.includes('business-logic-error')) {
  showAlert("Insufficient credits: " + response.data.detail);
  redirectToUpgradePage();
}
```

---

### Scenario 2: Validation Error - Multiple Fields

**Request:**
```javascript
POST /api/jobs/create/
{
  "title": "Dev",
  "salary_min": -1000
}
```

**Response (400):**
```json
{
  "type": "https://api.nidusjob.com/errors/validation-error",
  "title": "Validation Error",
  "status": 400,
  "detail": "The request body has validation errors",
  "instance": "/api/jobs/create/",
  "errors": {
    "title": ["Title must be between 10-200 characters."],
    "salary_min": ["Salary must be a positive number."]
  },
  "_legacy": {
    "success": false,
    "error": "validation_error",
    "message": "The request body has validation errors",
    "status_code": 400,
    "details": "The request body has validation errors",
    "errors": {
      "title": ["Title must be between 10-200 characters."],
      "salary_min": ["Salary must be a positive number."]
    }
  }
}
```

**Frontend Action:**
```javascript
if (response.status === 400 && response.data.errors) {
  Object.keys(response.data.errors).forEach(field => {
    const errors = response.data.errors[field];
    showFieldError(field, errors[0]); // Show first error message
  });
}
```

---

### Scenario 3: Authentication Error - Token Expired

**Request:**
```javascript
GET /api/jobs/my-jobs/
Headers: { "Authorization": "Bearer expired_token" }
```

**Response (401):**
```json
{
  "type": "https://api.nidusjob.com/errors/authentication-error",
  "title": "Authentication Error",
  "status": 401,
  "detail": "Token expired or invalid",
  "instance": "/api/jobs/my-jobs/",
  "_legacy": {
    "success": false,
    "error": "authentication_error",
    "message": "Token expired or invalid",
    "status_code": 401,
    "details": "Token expired or invalid"
  }
}
```

**Frontend Action:**
```javascript
if (response.status === 401) {
  clearAuthToken();
  redirectToLoginPage();
  showMessage("Your session has expired. Please login again.");
}
```

---

### Scenario 4: Rate Limit Error

**Response (429):**
```json
{
  "type": "https://api.nidusjob.com/errors/rate-limit-error",
  "title": "Rate Limit Exceeded",
  "status": 429,
  "detail": "Too many requests. Maximum 100 requests per hour allowed",
  "instance": "/api/jobs/search/",
  "_legacy": {
    "success": false,
    "error": "rate_limit_error",
    "message": "Too many requests. Maximum 100 requests per hour allowed",
    "status_code": 429,
    "details": "Too many requests. Maximum 100 requests per hour allowed"
  }
}
```

**Frontend Action:**
```javascript
if (response.status === 429) {
  showAlert("Too many requests. Please wait before trying again.");
  disableSubmitButton(60); // Disable for 60 seconds
}
```

---

### Scenario 5: Server Error - With Trace ID

**Response (500):**
```json
{
  "type": "https://api.nidusjob.com/errors/internal-server-error",
  "title": "Internal Server Error",
  "status": 500,
  "detail": "An unexpected error occurred during processing",
  "instance": "/api/ai/generate-job/",
  "trace_id": "58d32fbb-4d2f-448d-8fa6-2b90eadc04a9",
  "_legacy": {
    "success": false,
    "error": "internal_server_error",
    "message": "An unexpected error occurred during processing",
    "status_code": 500,
    "details": "An unexpected error occurred during processing"
  }
}
```

**Frontend Action:**
```javascript
if (response.status >= 500) {
  const traceId = response.data.trace_id;
  showAlert(`Server error. Support code: ${traceId}`);
  logErrorToSentry({ trace_id: traceId, endpoint: response.data.instance });
}
```

---

## Frontend Implementation Examples

### JavaScript/React Error Handler

```javascript
// utils/apiErrorHandler.js
export function handleApiError(error) {
  if (!error.response) {
    return {
      title: "Network Error",
      detail: "Could not connect to server",
      type: "network-error"
    };
  }

  const { status, data } = error.response;

  // RFC 7807 Format
  const errorResponse = {
    type: data.type,
    title: data.title,
    status: status,
    detail: data.detail,
    instance: data.instance,
    errors: data.errors || {},
    trace_id: data.trace_id || null
  };

  switch (status) {
    case 400:
      // Handle validation errors
      handleValidationError(errorResponse.errors);
      break;
    
    case 401:
      // Handle auth error
      clearAuthToken();
      redirectToLogin();
      break;
    
    case 403:
      // Handle permission error
      showAlert("You don't have permission to do this");
      break;
    
    case 404:
      // Handle not found
      showAlert("Resource not found: " + errorResponse.detail);
      break;
    
    case 422:
      // Handle business logic error
      showAlert("Cannot complete action: " + errorResponse.detail);
      break;
    
    case 429:
      // Handle rate limit
      showAlert("Too many requests. Please wait.");
      break;
    
    case 500:
      // Handle server error - show trace ID
      showAlert(`Server error. Contact support with code: ${errorResponse.trace_id}`);
      logToSentry(errorResponse);
      break;
    
    case 503:
      // Handle service unavailable
      showAlert("Service temporarily unavailable. Please try again later.");
      break;
    
    case 504:
      // Handle timeout
      showAlert("Request timed out. Please try again.");
      break;
  }

  return errorResponse;
}

function handleValidationError(errors) {
  if (!errors || Object.keys(errors).length === 0) return;

  Object.keys(errors).forEach(field => {
    const fieldErrors = errors[field];
    showFieldError(field, fieldErrors[0]); // Show first error
  });
}

function showFieldError(field, message) {
  // Clear existing error
  const fieldElement = document.querySelector(`[name="${field}"]`);
  if (fieldElement) {
    fieldElement.classList.add('error');
    const errorDiv = document.createElement('div');
    errorDiv.className = 'field-error';
    errorDiv.textContent = message;
    fieldElement.parentElement.appendChild(errorDiv);
  }
}
```

### React Hook Example

```javascript
// hooks/useApiRequest.js
import { useState } from 'react';
import { handleApiError } from '../utils/apiErrorHandler';

export function useApiRequest() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const request = async (config) => {
    try {
      setLoading(true);
      setError(null);
      
      const response = await axios(config);
      return response.data;
      
    } catch (err) {
      const apiError = handleApiError(err);
      setError(apiError);
      throw apiError;
      
    } finally {
      setLoading(false);
    }
  };

  return { request, loading, error };
}

// Usage in component
function CreateJobForm() {
  const { request, loading, error } = useApiRequest();

  const handleSubmit = async (formData) => {
    try {
      const response = await request({
        method: 'POST',
        url: '/api/jobs/create/',
        data: formData
      });
      
      if (response.success) {
        showSuccessMessage("Job created successfully");
        navigateTo('/jobs');
      }
    } catch (err) {
      // Error already handled by handleApiError
      if (err.status === 400 && err.errors) {
        // Show field errors
      }
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      {error && <div className="alert alert-danger">{error.detail}</div>}
      {/* Form fields */}
    </form>
  );
}
```

---

## Field-Level Validation Errors

### Example: Validation Error with Multiple Fields

```json
{
  "type": "https://api.nidusjob.com/errors/validation-error",
  "title": "Validation Error",
  "status": 400,
  "detail": "The request body has validation errors",
  "instance": "/api/jobs/create/",
  "errors": {
    "title": [
      "Title must be between 10-200 characters."
    ],
    "description": [
      "Description is required.",
      "Description must be at least 50 characters."
    ],
    "salary_min": [
      "Must be a positive number."
    ],
    "salary_max": [
      "Must be greater than salary_min."
    ]
  }
}
```

### Frontend: Display All Field Errors

```javascript
function displayValidationErrors(errors) {
  Object.entries(errors).forEach(([field, errorList]) => {
    const element = document.querySelector(`[name="${field}"]`);
    if (element) {
      // Add error class to field
      element.classList.add('is-invalid');
      
      // Display all error messages
      const errorContainer = document.createElement('div');
      errorContainer.className = 'invalid-feedback';
      errorList.forEach(errorMsg => {
        const errorLine = document.createElement('div');
        errorLine.textContent = errorMsg;
        errorContainer.appendChild(errorLine);
      });
      
      element.parentElement.appendChild(errorContainer);
    }
  });
}
```

---

## Trace ID for Debugging

### When Trace ID is Present (500+ Errors Only)

```json
{
  "type": "https://api.nidusjob.com/errors/internal-server-error",
  "title": "Internal Server Error",
  "status": 500,
  "detail": "An unexpected error occurred",
  "instance": "/api/ai/generate-job/",
  "trace_id": "58d32fbb-4d2f-448d-8fa6-2b90eadc04a9"
}
```

### Frontend: Log and Display Trace ID

```javascript
function handleServerError(response) {
  const traceId = response.data.trace_id;
  
  // Show user-friendly message with trace ID
  showAlert(
    `Something went wrong. Please try again or contact support.\n\nError Code: ${traceId}`
  );
  
  // Log to error tracking service
  console.error({
    endpoint: response.data.instance,
    time: new Date().toISOString(),
    trace_id: traceId
  });
  
  // Send to Sentry / DataDog
  Sentry.captureException(new Error("API Server Error"), {
    tags: { trace_id: traceId, endpoint: response.data.instance }
  });
}
```

---

## Backward Compatibility (_legacy)

### Old Format (Deprecated but Still Supported)

```json
{
  "success": false,
  "error": "validation_error",
  "message": "The request body has validation errors",
  "status_code": 400,
  "details": "The request body has validation errors",
  "errors": {
    "title": ["Title must be between 10-200 characters."]
  }
}
```

### New Format (RFC 7807 - Recommended)

```json
{
  "type": "https://api.nidusjob.com/errors/validation-error",
  "title": "Validation Error",
  "status": 400,
  "detail": "The request body has validation errors",
  "instance": "/api/jobs/create/",
  "errors": {
    "title": ["Title must be between 10-200 characters."]
  },
  "_legacy": {
    "success": false,
    "error": "validation_error",
    "message": "...",
    "status_code": 400,
    "details": "...",
    "errors": {
      "title": ["..."]
    }
  }
}
```

### Frontend: Support Both (Optional)

```javascript
function parseApiError(response) {
  // Prefer new RFC 7807 format
  if (response.data.type) {
    return {
      type: response.data.type,
      title: response.data.title,
      detail: response.data.detail,
      errors: response.data.errors,
      trace_id: response.data.trace_id
    };
  }
  
  // Fallback to legacy format
  if (response.data._legacy) {
    return {
      type: response.data._legacy.error,
      title: response.data._legacy.error,
      detail: response.data._legacy.message,
      errors: response.data._legacy.errors,
      trace_id: null
    };
  }
  
  // Last resort
  return {
    type: 'unknown-error',
    title: 'Unknown Error',
    detail: 'An unexpected error occurred',
    errors: {},
    trace_id: null
  };
}
```

---

## Quick Reference: Error Handling Decision Tree

```
API Error Received
    ↓
Is it a 4xx status?
    ├─ YES:
    │   ├─ Is it 400? → Validation Error (show field errors)
    │   ├─ Is it 401? → Auth Error (redirect to login)
    │   ├─ Is it 403? → Permission Error (show "Access Denied")
    │   ├─ Is it 404? → Not Found (show "Resource not found")
    │   ├─ Is it 422? → Business Logic Error (show detail message)
    │   └─ Is it 429? → Rate Limit (show "Too many requests")
    │
    └─ Is it a 5xx status?
        ├─ YES:
        │   ├─ Extract trace_id
        │   ├─ Show: "Error Code: {trace_id}"
        │   ├─ Log to Sentry with trace_id
        │   └─ Is it 503? → Service Unavailable (retry later)
        │   └─ Is it 504? → Timeout (retry smaller request)
        │
        └─ Unknown: Show detail field
```

---

## Summary for Frontend Team

✅ **All endpoints now return RFC 7807 format**
✅ **Field-level validation errors in `errors` object**
✅ **Server errors include `trace_id` for debugging**
✅ **Backward compatible via `_legacy` wrapper**
✅ **Use `status` + `type` to identify error category**
✅ **Display `detail` field to users**
✅ **Report `trace_id` to support for investigation**

---

## Questions?

📧 Contact Backend Team for:
- RFC 7807 implementation details
- Adding new error types
- Custom error fields for specific features
- Integration with error tracking (Sentry, etc.)

