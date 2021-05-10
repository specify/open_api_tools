"""A validator for request/response objects powered by OpenAPI schema."""

import json
import urllib.parse as urlparse
from json import JSONDecodeError
from typing import Callable, Dict, NamedTuple, Union
from urllib.parse import parse_qs
from openapi_core.contrib.requests import (
    RequestsOpenAPIRequest,
    RequestsOpenAPIResponseFactory,
)
from openapi_core.validation.request.validators import RequestValidator
from openapi_core.validation.response.validators import (
    ResponseValidator,
)
from requests import Request, Session

from open_api_tools.common.load_schema import Schema

session = Session()


class ErrorMessage(NamedTuple):
    """An error returned by the validator."""

    type: str
    title: str
    error_status: str
    url: str
    extra: Dict


class PreparedRequest(NamedTuple):
    """A successful prepared request."""

    type: str
    request: object
    openapi_request: object


def prepare_request(
    request_url: str,
    method: str,
    body: Union[Dict, None],
    error_callback: Callable[[ErrorMessage], None],
    schema: Schema,
) -> Union[PreparedRequest, ErrorMessage]:
    """Prepare request and validate the request URL.

    Args:
        request_url (str): request URL
        method (str): HTTP method name
        body (Union[Dict, None]: payload to send along with the request
        error_callback: function to call in case of an error
        schema (Schema): OpenAPI schema

    Returns:
        object: Prepared request or error message
    """
    request_validator = RequestValidator(schema.schema)
    parsed_url = urlparse.urlparse(request_url)
    base_url = request_url.split("?")[0]
    query_params_dict = parse_qs(parsed_url.query)

    request = Request(method, base_url, params=query_params_dict, data=body)
    openapi_request = RequestsOpenAPIRequest(request)
    request_url_validator = request_validator.validate(openapi_request)

    if request_url_validator.errors:
        error_message = request_url_validator.errors
        error_response = ErrorMessage(
            type="invalid_request_url",
            title="Invalid Request URL",
            error_status="Request URL does not meet the "
            + "OpenAPI Schema requirements",
            url=request_url,
            extra={
                "text": error_message,
            },
        )
        error_callback(error_response)
        return error_response

    return PreparedRequest(
        type="success",
        request=request,
        openapi_request=openapi_request,
    )


class FiledRequest(NamedTuple):
    """A successful filed request with a response."""

    type: str
    parsed_response: object


def file_request(
    request,
    openapi_request,
    request_url: str,
    error_callback: Callable[[ErrorMessage], None],
    schema: Schema,
) -> Union[ErrorMessage, FiledRequest]:
    """
    Send a prepared request and validate the response.

    Args:
        request: request object
        openapi_request: openapi request object
        request_url (str): request url
        error_callback: function to call in case of an error
        schema (Schema): OpenAPI schema

    Returns:
        Request response or error message
    """
    response_validator = ResponseValidator(schema.schema)
    prepared_request = request.prepare()
    response = session.send(prepared_request)

    # make sure that the server did not return an error
    if response.status_code != 200:
        error_response = ErrorMessage(
            type="invalid_response_code",
            title="Invalid Response",
            error_status="Response status code indicates an error has "
            + "occurred",
            url=request_url,
            extra={
                "status_code": response.status_code,
                "text": response.text,
            },
        )
        error_callback(error_response)
        return error_response

    # make sure the response is a valid JSON object
    try:
        parsed_response = json.loads(response.text)
    except JSONDecodeError:
        error_response = ErrorMessage(
            type="invalid_response_mime_type",
            title="Invalid response",
            error_status="Unable to parse JSON response",
            url=request_url,
            extra={
                "status_code": response.status_code,
                "text": response.text,
            },
        )
        error_callback(error_response)
        return error_response

    # validate the response against the schema
    formatted_response = RequestsOpenAPIResponseFactory.create(response)
    response_content_validator = response_validator.validate(
        openapi_request, formatted_response
    )

    if response_content_validator.errors:
        error_message = list(
            map(
                lambda e: e.schema_errors,
                response_content_validator.errors,
            )
        )
        error_response = ErrorMessage(
            type="invalid_response_schema",
            title="Invalid response schema",
            error_status="Response content does not meet the OpenAPI "
            + "Schema requirements",
            url=request_url,
            extra={
                "text": error_message,
                "parsed_response": parsed_response,
            },
        )
        error_callback(error_response)
        return error_response

    return FiledRequest(type="success", parsed_response=parsed_response)


def make_request(
    request_url: str,
    method: str,
    body: Union[Dict, None],
    error_callback: Callable[[ErrorMessage], None],
    schema: Schema,
):
    """
    Combine `prepared_request` and `file_request`.

    Prepare a request and send it, while running validation on each
    step.

    Args:
        request_url (str): request error
        method (str): HTTP method name
        body (Union[Dict, None]: payload to send along with the request
        error_callback: function to call in case of an error
        schema (Schema): OpenAPI schema

    Returns:
        Request response or error message
    """
    response = prepare_request(
        request_url,
        method,
        body,
        error_callback,
        schema
    )

    if response.type != "success":
        return response

    return file_request(
        response.request,
        response.openapi_request,
        request_url,
        error_callback,
        schema,
    )
