"""Application wire contracts; execution belongs to services.platform."""
from .kernel.contracts import check_schema, validate, read_json_delivery
from .kernel.models import Key
from .interfaces.http import Invocation, Document
