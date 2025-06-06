import json
import re
import textwrap

import pytest
import requests

from localstack.testing.pytest import markers
from localstack.utils.strings import short_uid, to_str
from localstack.utils.sync import retry
from localstack.utils.xml import is_valid_xml
from tests.aws.services.apigateway.apigateway_fixtures import api_invoke_url
from tests.aws.services.apigateway.conftest import APIGATEWAY_ASSUME_ROLE_POLICY
from tests.aws.services.apigateway.test_apigateway_basic import TEST_STAGE_NAME


@markers.aws.validated
def test_sqs_aws_integration(
    create_rest_apigw,
    sqs_create_queue,
    aws_client,
    create_role_with_policy,
    region_name,
    account_id,
    snapshot,
    sqs_collect_messages,
):
    snapshot.add_transformer(snapshot.transform.sqs_api())
    # create target SQS stream
    queue_name = f"queue-{short_uid()}"
    queue_url = sqs_create_queue(QueueName=queue_name)

    # create invocation role
    _, role_arn = create_role_with_policy(
        "Allow", "sqs:SendMessage", json.dumps(APIGATEWAY_ASSUME_ROLE_POLICY), "*"
    )

    api_id, _, root = create_rest_apigw(
        name=f"test-api-${short_uid()}",
        description="Test Integration with SQS",
    )

    resource_id = aws_client.apigateway.create_resource(
        restApiId=api_id,
        parentId=root,
        pathPart="sqs",
    )["id"]

    aws_client.apigateway.put_method(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        authorizationType="NONE",
    )

    aws_client.apigateway.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        type="AWS",
        integrationHttpMethod="POST",
        uri=f"arn:aws:apigateway:{region_name}:sqs:path/{account_id}/{queue_name}",
        credentials=role_arn,
        requestParameters={
            "integration.request.header.Content-Type": "'application/x-www-form-urlencoded'"
        },
        requestTemplates={"application/json": "Action=SendMessage&MessageBody=$input.body"},
        passthroughBehavior="NEVER",
    )

    aws_client.apigateway.put_method_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="200",
        responseModels={"application/json": "Empty"},
    )

    aws_client.apigateway.put_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="200",
        responseTemplates={"application/json": '{"message": "great success!"}'},
    )

    response = aws_client.apigateway.create_deployment(restApiId=api_id)
    deployment_id = response["id"]

    aws_client.apigateway.create_stage(
        restApiId=api_id,
        stageName=TEST_STAGE_NAME,
        deploymentId=deployment_id,
    )

    invocation_url = api_invoke_url(api_id=api_id, stage=TEST_STAGE_NAME, path="/sqs")

    def invoke_api(url, payload):
        kwargs = {"json": payload} if payload is not None else {}
        _response = requests.post(url, **kwargs)
        assert _response.ok
        content = _response.json()
        assert content == {"message": "great success!"}
        return content

    response_data = retry(
        invoke_api, sleep=2, retries=10, url=invocation_url, payload={"foo": "bar"}
    )
    snapshot.match("sqs-aws-integration-with-payload", response_data)
    response_data = retry(invoke_api, sleep=2, retries=10, url=invocation_url, payload=None)
    snapshot.match("sqs-aws-integration-without-payload", response_data)

    messages = sqs_collect_messages(queue_url=queue_url, expected=2, timeout=10)
    snapshot.match("sqs-messages", messages)


@markers.aws.validated
def test_sqs_request_and_response_xml_templates_integration(
    create_rest_apigw,
    sqs_create_queue,
    aws_client,
    create_role_with_policy,
    region_name,
    account_id,
    snapshot,
):
    queue_name = f"queue-{short_uid()}"
    sqs_create_queue(QueueName=queue_name)

    # create invocation role
    _, role_arn = create_role_with_policy(
        "Allow", "sqs:SendMessage", json.dumps(APIGATEWAY_ASSUME_ROLE_POLICY), "*"
    )

    api_id, _, root = create_rest_apigw(
        name=f"test-api-${short_uid()}",
    )

    resource_id = aws_client.apigateway.create_resource(
        restApiId=api_id,
        parentId=root,
        pathPart="sqs",
    )["id"]

    aws_client.apigateway.put_method(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        authorizationType="NONE",
    )

    aws_client.apigateway.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        type="AWS",
        integrationHttpMethod="POST",
        uri=f"arn:aws:apigateway:{region_name}:sqs:path/{account_id}/{queue_name}",
        credentials=role_arn,
        requestParameters={
            "integration.request.header.Content-Type": "'application/x-www-form-urlencoded'"
        },
        requestTemplates={"application/json": "Action=SendMessage&MessageBody=$input.body"},
        passthroughBehavior="NEVER",
    )

    aws_client.apigateway.put_method_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="200",
        responseModels={"application/json": "Empty"},
    )

    aws_client.apigateway.put_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="200",
        responseTemplates={
            "application/json": """
            #set($responseBody = $input.path('$.SendMessageResponse'))
            #set($requestId = $input.path('$.SendMessageResponse.ResponseMetadata.RequestId'))
            #set($messageId = $responseBody.SendMessageResult.MessageId)
            {
            "requestId": "$requestId",
            "messageId": "$messageId"
            }
            """
        },
    )

    response = aws_client.apigateway.create_deployment(
        restApiId=api_id,
    )
    deployment_id = response["id"]

    aws_client.apigateway.create_stage(
        restApiId=api_id, stageName=TEST_STAGE_NAME, deploymentId=deployment_id
    )

    invocation_url = api_invoke_url(api_id=api_id, stage=TEST_STAGE_NAME, path="/sqs")

    def invoke_api(url, validate_xml=None):
        _response = requests.post(url, data="<xml>Hello World</xml>", verify=False)
        if validate_xml:
            assert is_valid_xml(_response.content.decode("utf-8"))
            return _response

        assert _response.ok
        return _response

    response_data = retry(invoke_api, sleep=2, retries=10, url=invocation_url)
    snapshot.match("sqs-json-response", response_data.json())

    # patch integration request parameters to use Accept header with "application/xml"
    # and remove response templates
    aws_client.apigateway.update_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        patchOperations=[
            {
                "op": "add",
                "path": "/requestParameters/integration.request.header.Accept",
                "value": "'application/xml'",
            }
        ],
    )

    aws_client.apigateway.update_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="200",
        patchOperations=[
            {
                "op": "remove",
                "path": "/responseTemplates/application~1json",
                "value": "application/json",
            }
        ],
    )

    # create deployment and update stage for re-deployment
    deployment = aws_client.apigateway.create_deployment(
        restApiId=api_id,
    )

    aws_client.apigateway.update_stage(
        restApiId=api_id,
        stageName=TEST_STAGE_NAME,
        patchOperations=[{"op": "replace", "path": "/deploymentId", "value": deployment["id"]}],
    )

    response = retry(invoke_api, sleep=2, retries=10, url=invocation_url, validate_xml=True)

    xml_body = to_str(response.content)
    # snapshotting would be great, but the response differs from AWS on the XML on the element order
    assert re.search("<MessageId>.*</MessageId>", xml_body)
    assert re.search("<MD5OfMessageBody>.*</MD5OfMessageBody>", xml_body)
    assert re.search("<RequestId>.*</RequestId>", xml_body)


@pytest.mark.parametrize("message_attribute", ["MessageAttribute", "MessageAttributes"])
@markers.aws.validated
def test_sqs_aws_integration_with_message_attribute(
    create_rest_apigw,
    sqs_create_queue,
    aws_client,
    create_role_with_policy,
    region_name,
    account_id,
    snapshot,
    message_attribute,
):
    # create target SQS stream
    queue_name = f"queue-{short_uid()}"
    queue_url = sqs_create_queue(QueueName=queue_name)

    # create invocation role
    _, role_arn = create_role_with_policy(
        "Allow", "sqs:SendMessage", json.dumps(APIGATEWAY_ASSUME_ROLE_POLICY), "*"
    )

    api_id, _, root = create_rest_apigw(
        name=f"test-api-${short_uid()}",
        description="Test Integration with SQS",
    )

    aws_client.apigateway.put_method(
        restApiId=api_id,
        resourceId=root,
        httpMethod="POST",
        authorizationType="NONE",
    )

    aws_client.apigateway.put_integration(
        restApiId=api_id,
        resourceId=root,
        httpMethod="POST",
        type="AWS",
        integrationHttpMethod="POST",
        uri=f"arn:aws:apigateway:{region_name}:sqs:path/{account_id}/{queue_name}",
        credentials=role_arn,
        requestParameters={
            "integration.request.header.Content-Type": "'application/x-www-form-urlencoded'"
        },
        requestTemplates={
            "application/json": (
                "Action=SendMessage&MessageBody=$input.body&"
                f"{message_attribute}.1.Name=user-agent&"
                f"{message_attribute}.1.Value.DataType=String&"
                f"{message_attribute}.1.Value.StringValue=$input.params('HeaderFoo')"
            )
        },
        passthroughBehavior="NEVER",
    )

    aws_client.apigateway.put_method_response(
        restApiId=api_id,
        resourceId=root,
        httpMethod="POST",
        statusCode="200",
        responseModels={"application/json": "Empty"},
    )

    aws_client.apigateway.put_integration_response(
        restApiId=api_id,
        resourceId=root,
        httpMethod="POST",
        statusCode="200",
    )

    aws_client.apigateway.create_deployment(restApiId=api_id, stageName=TEST_STAGE_NAME)
    invocation_url = api_invoke_url(api_id=api_id, stage=TEST_STAGE_NAME, path="/")

    def invoke_api(url):
        _response = requests.post(url, json={"foo": "bar"}, headers={"HeaderFoo": "BAR-Header"})
        assert _response.ok

    retry(invoke_api, sleep=2, retries=10, url=invocation_url)

    def get_sqs_message():
        messages = aws_client.sqs.receive_message(
            QueueUrl=queue_url, MessageAttributeNames=["All"]
        ).get("Messages", [])
        assert 1 == len(messages)
        return messages[0]

    message = retry(get_sqs_message, sleep=2, retries=10)
    snapshot.match("sqs-message-body", message["Body"])
    snapshot.match("sqs-message-attributes", message["MessageAttributes"])


@markers.aws.validated
@markers.snapshot.skip_snapshot_verify(
    paths=[
        # FIXME: those are minor parity gap in how we handle printing out VTL Map when they are nested inside bigger maps
        "$..context.identity",
        "$..context.requestOverride",
        "$..context.responseOverride",
        "$..requestOverride.header",
        "$..requestOverride.path",
        "$..requestOverride.querystring",
        "$..responseOverride.header",
        "$..responseOverride.path",
        "$..responseOverride.status",
    ]
)
def test_sqs_amz_json_protocol(
    create_rest_apigw,
    sqs_create_queue,
    aws_client,
    create_role_with_policy,
    region_name,
    account_id,
    snapshot,
    sqs_collect_messages,
):
    snapshot.add_transformer(snapshot.transform.sqs_api())
    snapshot.add_transformers_list(
        [
            snapshot.transform.key_value("MD5OfBody"),
            snapshot.transform.key_value("resourceId"),
            snapshot.transform.key_value("extendedRequestId"),
            snapshot.transform.key_value("requestTime"),
            snapshot.transform.key_value("requestTimeEpoch", reference_replacement=False),
            snapshot.transform.key_value("domainName"),
            snapshot.transform.key_value("deploymentId"),
            snapshot.transform.key_value("apiId"),
            snapshot.transform.key_value("sourceIp"),
        ]
    )

    # create target SQS stream
    queue_name = f"queue-{short_uid()}"
    queue_url = sqs_create_queue(QueueName=queue_name)

    # create invocation role
    _, role_arn = create_role_with_policy(
        "Allow", "sqs:SendMessage", json.dumps(APIGATEWAY_ASSUME_ROLE_POLICY), "*"
    )

    api_id, _, root = create_rest_apigw(
        name=f"test-api-{short_uid()}",
        description="Test Integration with SQS",
    )

    resource_id = aws_client.apigateway.create_resource(
        restApiId=api_id,
        parentId=root,
        pathPart="sqs",
    )["id"]

    aws_client.apigateway.put_method(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        authorizationType="NONE",
    )

    # we need to inline the JSON object because VTL does not handle newlines very well :/
    context_template = textwrap.dedent(f"""
        {{
            "QueueUrl": "{queue_url}",
            "MessageBody": "{{\\"context\\": {{#foreach( $key in $context.keySet() )\\"$key\\": \\"$context.get($key)\\"#if($foreach.hasNext),#end#end}},\\"identity\\": {{#foreach( $key in $context.identity.keySet() )\\"$key\\": \\"$context.identity.get($key)\\"#if($foreach.hasNext),#end#end}},\\"requestOverride\\": {{#foreach( $key in $context.requestOverride.keySet() )\\"$key\\": \\"$context.requestOverride.get($key)\\"#if($foreach.hasNext),#end#end}},\\"responseOverride\\": {{#foreach( $key in $context.responseOverride.keySet() )\\"$key\\": \\"$context.responseOverride.get($key)\\"#if($foreach.hasNext),#end#end}},\\"authorizer_keys\\": {{#foreach( $key in $context.authorizer.keySet() )\\"$key\\": \\"$util.escapeJavaScript($context.authorizer.get($key))\\"#if($foreach.hasNext),#end#end}}}}"}}
    """)

    aws_client.apigateway.put_integration(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        type="AWS",
        integrationHttpMethod="POST",
        uri=f"arn:aws:apigateway:{region_name}:sqs:path/{account_id}/{queue_name}",
        credentials=role_arn,
        requestParameters={
            "integration.request.header.Content-Type": "'application/x-amz-json-1.0'",
            "integration.request.header.X-Amz-Target": "'AmazonSQS.SendMessage'",
        },
        requestTemplates={"application/json": context_template},
        passthroughBehavior="NEVER",
    )

    aws_client.apigateway.put_method_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="200",
        responseModels={"application/json": "Empty"},
    )
    aws_client.apigateway.put_method_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="400",
        responseModels={"application/json": "Empty"},
    )

    aws_client.apigateway.put_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="200",
        responseTemplates={"application/json": '{"message": "great success!"}'},
    )

    aws_client.apigateway.put_integration_response(
        restApiId=api_id,
        resourceId=resource_id,
        httpMethod="POST",
        statusCode="400",
        responseTemplates={"application/json": '{"message": "failure :("}'},
        selectionPattern="400",
    )

    aws_client.apigateway.create_deployment(restApiId=api_id, stageName=TEST_STAGE_NAME)

    invocation_url = api_invoke_url(api_id=api_id, stage=TEST_STAGE_NAME, path="/sqs")

    def invoke_api(url):
        _response = requests.post(url, headers={"User-Agent": "python/requests/tests"})
        assert _response.ok
        content = _response.json()
        assert content == {"message": "great success!"}
        return content

    retry(invoke_api, sleep=2, retries=10, url=invocation_url)

    messages = sqs_collect_messages(
        queue_url=queue_url, expected=1, timeout=10, wait_time_seconds=5
    )
    snapshot.match("sqs-messages", messages)
