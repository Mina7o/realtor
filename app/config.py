import os


class Settings:
    port: int = int(os.getenv("PORT", 5001))
    mongo_uri: str = os.getenv("MDB_CONNECTION_STRING", "mongodb://localhost:27017")
    attom_api_key: str = os.getenv("ATTOM_API_KEY", "")
    otlp_endpoint: str = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    batch_id: str = os.getenv("OTEL_BATCH_ID", "")
    otel_service_name: str = os.getenv("OTEL_SERVICE_NAME", "realtor-flask")
    otel_protocol: str = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")


settings = Settings()
