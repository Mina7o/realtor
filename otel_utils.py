import importlib, sys
sys.modules[__name__] = importlib.import_module('app.utils.otel_utils')
