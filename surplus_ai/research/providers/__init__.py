from __future__ import annotations

from surplus_ai.research.providers.arcgis import (
    ArcGISFeatureServerProvider,
    ArcGISProviderOptions,
)
from surplus_ai.research.providers.base import AbstractPropertyRecordProvider
from surplus_ai.research.providers.credentials_missing import CredentialsMissingProvider
from surplus_ai.research.providers.manual import ManualLookupProvider
from surplus_ai.research.providers.null import NullProvider
from surplus_ai.research.providers.rest_json import RestJsonProvider, RestJsonProviderOptions
from surplus_ai.research.providers.socrata import SocrataOpenDataProvider, SocrataProviderOptions

__all__ = [
    "AbstractPropertyRecordProvider",
    "ArcGISFeatureServerProvider",
    "ArcGISProviderOptions",
    "CredentialsMissingProvider",
    "ManualLookupProvider",
    "NullProvider",
    "RestJsonProvider",
    "RestJsonProviderOptions",
    "SocrataOpenDataProvider",
    "SocrataProviderOptions",
]
