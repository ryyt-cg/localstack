from localstack import config
from localstack.aws import handlers
from localstack.aws.api import RequestContext
from localstack.aws.chain import HandlerChain
from localstack.aws.handlers.metric_handler import MetricHandler
from localstack.aws.handlers.service_plugin import ServiceLoader, ServiceLoaderForDataPlane
from localstack.http.trace import TracingHandlerChain
from localstack.services.plugins import SERVICE_PLUGINS, ServiceManager, ServicePluginManager
from localstack.utils.ssl import create_ssl_cert, install_predefined_cert_if_available

from .gateway import Gateway
from .handlers.fallback import EmptyResponseHandler
from .handlers.service import ServiceRequestRouter


class LocalstackAwsGateway(Gateway):
    def __init__(self, service_manager: ServiceManager = None) -> None:
        super().__init__(context_class=RequestContext)

        # basic server components
        self.service_manager = service_manager or ServicePluginManager()
        self.service_request_router = ServiceRequestRouter()
        # lazy-loads services into the router
        load_service = ServiceLoader(self.service_manager, self.service_request_router)
        load_service_for_data_plane = ServiceLoaderForDataPlane(load_service)

        metric_collector = MetricHandler()
        # the main request handler chain
        self.request_handlers.extend(
            [
                handlers.add_internal_request_params,
                handlers.handle_runtime_shutdown,
                metric_collector.create_metric_handler_item,
                load_service_for_data_plane,
                handlers.preprocess_request,
                handlers.enforce_cors,
                handlers.content_decoder,  # depends on preprocess_request for the S3 service
                handlers.validate_request_schema,  # validate request schema for public LS endpoints
                handlers.serve_localstack_resources,  # try to serve endpoints in /_localstack
                handlers.serve_edge_router_rules,
                # start aws handler chain
                handlers.parse_service_name,
                handlers.parse_pre_signed_url_request,
                handlers.inject_auth_header_if_missing,
                handlers.add_region_from_header,
                handlers.rewrite_region,
                handlers.add_account_id,
                handlers.parse_trace_context,
                handlers.parse_service_request,
                metric_collector.record_parsed_request,
                handlers.serve_custom_service_request_handlers,
                load_service,  # once we have the service request we can make sure we load the service
                self.service_request_router,  # once we know the service is loaded we can route the request
                # if the chain is still running, set an empty response
                EmptyResponseHandler(404, b'{"message": "Not Found"}'),
            ]
        )

        # exception handlers in the chain
        self.exception_handlers.extend(
            [
                handlers.log_exception,
                handlers.serve_custom_exception_handlers,
                handlers.handle_service_exception,
                handlers.handle_internal_failure,
            ]
        )

        # response post-processing
        self.response_handlers.extend(
            [
                handlers.validate_response_schema,  # validate response schema for public LS endpoints
                handlers.modify_service_response,
                handlers.parse_service_response,
                handlers.run_custom_response_handlers,
                handlers.add_cors_response_headers,
                handlers.log_response,
                handlers.count_service_request,
                metric_collector.update_metric_collection,
            ]
        )

        # request chain finalization
        self.finalizers.extend(
            [
                handlers.set_close_connection_header,
                handlers.run_custom_finalizers,
            ]
        )

    def new_chain(self) -> HandlerChain:
        if config.DEBUG_HANDLER_CHAIN:
            return TracingHandlerChain(
                self.request_handlers,
                self.response_handlers,
                self.finalizers,
                self.exception_handlers,
            )
        return super().new_chain()


def main():
    """
    Serve the LocalstackGateway with the default configuration directly through hypercorn. This is mostly for
    development purposes and documentation on how to serve the Gateway.
    """
    from .serving.hypercorn import serve

    use_ssl = True
    port = 4566

    # serve the LocalStackAwsGateway in a dev app
    from localstack.utils.bootstrap import setup_logging

    setup_logging()

    if use_ssl:
        install_predefined_cert_if_available()
        _, cert_file_name, key_file_name = create_ssl_cert(serial_number=port)
        ssl_creds = (cert_file_name, key_file_name)
    else:
        ssl_creds = None

    gw = LocalstackAwsGateway(SERVICE_PLUGINS)

    serve(gw, use_reloader=True, port=port, ssl_creds=ssl_creds)


if __name__ == "__main__":
    main()
