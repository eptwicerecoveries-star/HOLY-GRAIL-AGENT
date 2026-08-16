"""Generic HTTPS GET JSON property provider. Builds endpoints from config only."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from surplus_ai.research.endpoint import (
    parse_socrata_number_identity,
    rest_json_number_identity,
    rest_json_resource_url,
    validate_rest_json_domain,
    validate_rest_json_field_key,
    validate_rest_json_path,
    validate_rest_json_query_param,
    validate_rest_json_records_path,
)
from surplus_ai.research.http import HttpGetResult, HttpGetter, ResearchHttpClient
from surplus_ai.research.mapping import (
    AMBIGUOUS_MATCH_CONFIDENCE,
    EXACT_MATCH_CONFIDENCE,
    OWNER_MISMATCH_CONFIDENCE,
    atom,
    filter_identity_matches,
    owner_normalize,
)
from surplus_ai.research.models import (
    PropertyLookupQuery,
    ProviderOutcome,
    ProviderOutcomeStatus,
    utc_now,
)
from surplus_ai.research.providers.base import AbstractPropertyRecordProvider

_MAX_QUERY_LIMIT = 100

_ERROR_DETAIL = {
    "automated_access_not_verified": (
        "This live provider is not internally approved for automated access. "
        "The flag is an operational gate only, not legal permission."
    ),
    "missing_identity": (
        "Live REST JSON lookup requires a parcel identifier, or a configured account "
        "identifier on the query. Owner-only and address-only search are not enabled."
    ),
    "invalid_identity_format": (
        "The lookup identifier is not valid for the configured REST JSON value type."
    ),
    "timeout": "The provider request timed out.",
    "network_failure": "The provider connection failed.",
    "tls_failure": "TLS certificate validation failed. The request was not retried.",
    "dns_resolution_failed": "The provider hostname could not be resolved.",
    "unsafe_resolved_address": (
        "The provider hostname resolved to a disallowed destination. The request was not sent."
    ),
    "unsafe_url": "The request URL was rejected by the HTTPS/host policy.",
    "http_redirect": "The provider returned a redirect. Redirects are not followed.",
    "response_too_large": "The provider response exceeded the maximum allowed size.",
    "http_400": "The provider rejected the request (HTTP 400).",
    "http_401": "The provider returned HTTP 401.",
    "http_403": "The provider returned HTTP 403.",
    "http_404": "The configured REST endpoint was not found (HTTP 404).",
    "http_5xx": "The provider returned an HTTP 5xx error.",
    "malformed_provider_response": "The provider returned malformed JSON.",
    "schema_mismatch": "The provider response is missing a configured field.",
    "result_incomplete": (
        "The REST JSON response exceeded the configured result bound. "
        "The result is incomplete and was not used as a unique match."
    ),
}


class RestJsonIdentityValueType(str, Enum):
    """Configured REST JSON identity literal type. Never inferred from a live API."""

    TEXT = "text"
    NUMBER = "number"


class RestJsonProviderOptions(BaseModel):
    """Strict generic REST JSON adapter config. Unknown keys are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str
    path: str
    parcel_query_param: str
    parcel_field: str
    owner_field: str
    account_query_param: str | None = None
    account_field: str | None = None
    situs_address_field: str | None = None
    mailing_address_field: str | None = None
    record_id_field: str | None = None
    select_fields: tuple[str, ...]
    records_path: tuple[str, ...] = ()
    limit_query_param: str | None = None
    query_limit: int = Field(default=10, ge=1, le=_MAX_QUERY_LIMIT)
    parcel_value_type: RestJsonIdentityValueType = RestJsonIdentityValueType.TEXT
    account_value_type: RestJsonIdentityValueType = RestJsonIdentityValueType.TEXT
    source_organization: str | None = None
    verified_for_automated_access: bool = False
    access_reviewed_on: date | None = None

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        return validate_rest_json_domain(value)

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        return validate_rest_json_path(value)

    @field_validator("parcel_query_param")
    @classmethod
    def _parcel_query_param(cls, value: str) -> str:
        return validate_rest_json_query_param(value)

    @field_validator("account_query_param", "limit_query_param")
    @classmethod
    def _optional_query_param(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return validate_rest_json_query_param(value)

    @field_validator("parcel_field", "owner_field")
    @classmethod
    def _required_field(cls, value: str) -> str:
        return validate_rest_json_field_key(value)

    @field_validator(
        "account_field",
        "situs_address_field",
        "mailing_address_field",
        "record_id_field",
    )
    @classmethod
    def _optional_field(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return validate_rest_json_field_key(value)

    @field_validator("select_fields", mode="before")
    @classmethod
    def _select_fields(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            raise ValueError("select_fields must be a list of identifiers")
        if not value:
            raise ValueError("select_fields must be a non-empty list")
        parsed = [validate_rest_json_field_key(str(item)) for item in value]
        return tuple(parsed)

    @field_validator("records_path", mode="before")
    @classmethod
    def _records_path(cls, value: object) -> object:
        return validate_rest_json_records_path(value)

    @field_validator("verified_for_automated_access", mode="before")
    @classmethod
    def _verified_bool(cls, value: object) -> object:
        if isinstance(value, bool):
            return value
        raise ValueError("verified_for_automated_access must be a boolean")

    @field_validator("access_reviewed_on", mode="before")
    @classmethod
    def _reviewed_on(cls, value: object) -> object:
        if value is None or isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            raise ValueError("access_reviewed_on must be an ISO date (YYYY-MM-DD)")
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(
                    "access_reviewed_on must be an ISO date (YYYY-MM-DD)"
                ) from exc
        raise ValueError("access_reviewed_on must be an ISO date (YYYY-MM-DD)")

    @field_validator("source_organization")
    @classmethod
    def _org(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("query_limit", mode="before")
    @classmethod
    def _limit_type(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("query_limit must be an integer")
        return value

    @field_validator("parcel_value_type", "account_value_type", mode="before")
    @classmethod
    def _identity_value_type(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("identity value type must be 'text' or 'number'")
        return value

    @model_validator(mode="after")
    def _cross_field_rules(self) -> RestJsonProviderOptions:
        if self.verified_for_automated_access:
            if not self.source_organization:
                raise ValueError(
                    "verified_for_automated_access=true requires source_organization"
                )
            if self.access_reviewed_on is None:
                raise ValueError(
                    "verified_for_automated_access=true requires access_reviewed_on"
                )
        account_param = self.account_query_param
        account_field = self.account_field
        if (account_param is None) != (account_field is None):
            raise ValueError(
                "account_query_param and account_field must both be set or both omitted"
            )
        mapped = [
            field
            for field in (
                self.parcel_field,
                self.owner_field,
                self.account_field,
                self.situs_address_field,
                self.mailing_address_field,
                self.record_id_field,
            )
            if field
        ]
        missing = [field for field in mapped if field not in self.select_fields]
        if missing:
            raise ValueError(
                "select_fields must include every configured mapped field; "
                f"missing {missing}"
            )
        return self


class RestJsonProvider(AbstractPropertyRecordProvider):
    """County-agnostic generic REST JSON adapter. Retry ownership stays in Phase 6B."""

    def __init__(
        self,
        name: str,
        options: RestJsonProviderOptions,
        *,
        http: HttpGetter | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.name = name
        self._options = options
        self._http = http or ResearchHttpClient()
        self._now = now

    def supports(self, state: str, county_slug: str) -> bool:
        return bool(state) and bool(county_slug)

    def lookup(self, query: PropertyLookupQuery) -> ProviderOutcome:
        source_url = rest_json_resource_url(self._options.domain, self._options.path)
        if not self._options.verified_for_automated_access:
            return self._error(
                "automated_access_not_verified",
                source_url=source_url,
                retryable=False,
                review=True,
            )

        identity = self._identity(query)
        if identity is None:
            if self._blank_number_identity(query):
                return self._error(
                    "invalid_identity_format",
                    source_url=source_url,
                    retryable=False,
                    review=True,
                )
            return self._error(
                "missing_identity",
                source_url=source_url,
                retryable=False,
                review=True,
            )
        query_param, match_field, match_value, match_mode, value_type = identity
        try:
            param_value = self._param_value(match_value, value_type)
        except ValueError:
            return self._error(
                "invalid_identity_format",
                source_url=source_url,
                retryable=False,
                review=True,
            )
        params = self._query_params(query_param, param_value)
        result = self._http.get(source_url, params=params, headers={})
        retrieved_at = self._now()
        return self._map_result(
            result,
            query=query,
            source_url=source_url,
            match_field=match_field,
            match_value=match_value,
            match_mode=match_mode,
            value_type=value_type,
            retrieved_at=retrieved_at,
        )

    def _identity(
        self, query: PropertyLookupQuery
    ) -> tuple[str, str, str, str, RestJsonIdentityValueType] | None:
        parcel_raw = query.parcel_id
        if parcel_raw is not None and parcel_raw.strip():
            return (
                self._options.parcel_query_param,
                self._options.parcel_field,
                self._identity_value(parcel_raw, self._options.parcel_value_type),
                "exact_parcel",
                self._options.parcel_value_type,
            )
        account_raw = query.account_id
        if (
            account_raw is not None
            and account_raw.strip()
            and self._options.account_query_param
            and self._options.account_field
        ):
            return (
                self._options.account_query_param,
                self._options.account_field,
                self._identity_value(account_raw, self._options.account_value_type),
                "exact_account",
                self._options.account_value_type,
            )
        return None

    def _identity_value(self, raw: str, value_type: RestJsonIdentityValueType) -> str:
        if value_type is RestJsonIdentityValueType.NUMBER:
            return raw
        return raw.strip()

    def _blank_number_identity(self, query: PropertyLookupQuery) -> bool:
        parcel_raw = query.parcel_id
        if parcel_raw is not None and not parcel_raw.strip():
            return self._options.parcel_value_type is RestJsonIdentityValueType.NUMBER
        if (parcel_raw or "").strip():
            return False
        account_raw = query.account_id
        if (
            account_raw is not None
            and not account_raw.strip()
            and self._options.account_field
            and self._options.account_query_param
        ):
            return self._options.account_value_type is RestJsonIdentityValueType.NUMBER
        return False

    def _param_value(self, value: str, value_type: RestJsonIdentityValueType) -> str:
        if value_type is RestJsonIdentityValueType.NUMBER:
            return rest_json_number_identity(value)
        return value

    def _query_params(self, query_param: str, param_value: str) -> dict[str, str]:
        params = {query_param: param_value}
        limit_param = self._options.limit_query_param
        if limit_param:
            params[limit_param] = str(self._options.query_limit)
        return params

    def _map_result(
        self,
        result: HttpGetResult,
        *,
        query: PropertyLookupQuery,
        source_url: str,
        match_field: str,
        match_value: str,
        match_mode: str,
        value_type: RestJsonIdentityValueType,
        retrieved_at: datetime,
    ) -> ProviderOutcome:
        mapped = self._map_transport_or_status(result, source_url=source_url)
        if mapped is not None:
            return mapped

        try:
            payload = json.loads(result.body.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._error(
                "malformed_provider_response",
                source_url=source_url,
                retryable=False,
                review=True,
                http_status=result.status_code,
            )

        records = self._extract_records(
            payload, source_url=source_url, http_status=result.status_code
        )
        if isinstance(records, ProviderOutcome):
            return records

        result_count = len(records)
        if result_count > self._options.query_limit:
            return self._error(
                "result_incomplete",
                source_url=source_url,
                retryable=False,
                review=True,
                http_status=result.status_code,
                extra={"result_count": result_count},
            )

        if records:
            missing = self._missing_schema_fields(records)
            if missing:
                return self._error(
                    "schema_mismatch",
                    source_url=source_url,
                    retryable=False,
                    review=True,
                    http_status=result.status_code,
                    extra={"missing_fields": missing, "result_count": result_count},
                )
            if value_type is RestJsonIdentityValueType.NUMBER and self._unusable_number_identity(
                records, match_field
            ):
                return self._error(
                    "schema_mismatch",
                    source_url=source_url,
                    retryable=False,
                    review=True,
                    http_status=result.status_code,
                    extra={"missing_fields": [match_field], "result_count": result_count},
                )

        matched = filter_identity_matches(
            records,
            field=match_field,
            expected=match_value,
            numeric=value_type is RestJsonIdentityValueType.NUMBER,
        )
        return self._match_outcome(
            matched,
            query=query,
            source_url=source_url,
            match_mode=match_mode,
            retrieved_at=retrieved_at,
            http_status=result.status_code or 200,
            result_count=len(matched),
        )

    def _extract_records(
        self,
        payload: object,
        *,
        source_url: str,
        http_status: int | None,
    ) -> list[Mapping[str, object]] | ProviderOutcome:
        current: object = payload
        if not self._options.records_path:
            if not isinstance(current, list):
                return self._error(
                    "malformed_provider_response",
                    source_url=source_url,
                    retryable=False,
                    review=True,
                    http_status=http_status,
                )
            return self._coerce_record_list(
                current, source_url=source_url, http_status=http_status
            )

        for key in self._options.records_path:
            if not isinstance(current, dict):
                return self._error(
                    "malformed_provider_response",
                    source_url=source_url,
                    retryable=False,
                    review=True,
                    http_status=http_status,
                )
            if key not in current:
                return self._error(
                    "malformed_provider_response",
                    source_url=source_url,
                    retryable=False,
                    review=True,
                    http_status=http_status,
                )
            current = current[key]

        if not isinstance(current, list):
            return self._error(
                "malformed_provider_response",
                source_url=source_url,
                retryable=False,
                review=True,
                http_status=http_status,
            )
        return self._coerce_record_list(
            current, source_url=source_url, http_status=http_status
        )

    def _coerce_record_list(
        self,
        items: list[object],
        *,
        source_url: str,
        http_status: int | None,
    ) -> list[Mapping[str, object]] | ProviderOutcome:
        rows: list[Mapping[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                return self._error(
                    "malformed_provider_response",
                    source_url=source_url,
                    retryable=False,
                    review=True,
                    http_status=http_status,
                )
            rows.append(item)
        return rows

    def _map_transport_or_status(
        self, result: HttpGetResult, *, source_url: str
    ) -> ProviderOutcome | None:
        code = result.error_code
        if code == "timeout":
            return self._timeout(source_url=source_url)
        if code == "http_redirect":
            return self._error(
                "http_redirect",
                source_url=source_url,
                retryable=False,
                review=True,
                http_status=result.status_code,
            )
        if code == "response_too_large":
            return self._error(
                "response_too_large",
                source_url=source_url,
                retryable=False,
                review=True,
                http_status=result.status_code,
            )
        if code == "tls_failure":
            return self._error(
                "tls_failure",
                source_url=source_url,
                retryable=False,
                review=True,
            )
        if code == "network_failure":
            return self._error(
                "network_failure",
                source_url=source_url,
                retryable=result.retryable,
                review=True,
            )
        if code == "dns_resolution_failed":
            return self._error(
                "dns_resolution_failed",
                source_url=source_url,
                retryable=True,
                review=True,
            )
        if code == "unsafe_resolved_address":
            return self._error(
                "unsafe_resolved_address",
                source_url=source_url,
                retryable=False,
                review=True,
            )
        if code == "unsafe_url":
            return self._error(
                "unsafe_url",
                source_url=source_url,
                retryable=False,
                review=True,
            )
        if code is not None:
            return self._error(
                code if code in _ERROR_DETAIL else "network_failure",
                source_url=source_url,
                retryable=False,
                review=True,
            )

        status = result.status_code
        if status is None:
            return self._error(
                "network_failure",
                source_url=source_url,
                retryable=True,
                review=True,
            )
        if 300 <= status < 400:
            return self._error(
                "http_redirect",
                source_url=source_url,
                retryable=False,
                review=True,
                http_status=status,
            )
        if status == 408:
            return self._timeout(source_url=source_url, http_status=status)
        if status == 429:
            return ProviderOutcome(
                status=ProviderOutcomeStatus.RATE_LIMITED,
                found=False,
                source_url=source_url,
                error_code="rate_limited",
                error_detail="The provider returned HTTP 429.",
                requires_human_review=True,
                cacheable=False,
                retryable=True,
                raw_response=self._provenance(
                    http_status=status, result_count=0, retrieved_at=self._now()
                ),
            )
        if status == 400:
            return self._error(
                "http_400", source_url=source_url, retryable=False, review=True, http_status=status
            )
        if status == 401:
            return self._error(
                "http_401", source_url=source_url, retryable=False, review=True, http_status=status
            )
        if status == 403:
            return self._error(
                "http_403", source_url=source_url, retryable=False, review=True, http_status=status
            )
        if status == 404:
            return self._error(
                "http_404", source_url=source_url, retryable=False, review=True, http_status=status
            )
        if 500 <= status <= 599:
            return self._error(
                "http_5xx", source_url=source_url, retryable=True, review=True, http_status=status
            )
        if status >= 400:
            return self._error(
                f"http_{status}",
                source_url=source_url,
                retryable=False,
                review=True,
                http_status=status,
            )
        if status != 200:
            return self._error(
                f"http_{status}",
                source_url=source_url,
                retryable=False,
                review=True,
                http_status=status,
            )
        return None

    def _missing_schema_fields(self, rows: Sequence[Mapping[str, object]]) -> list[str]:
        required = [
            field
            for field in (
                self._options.parcel_field,
                self._options.owner_field,
                self._options.account_field,
                self._options.situs_address_field,
                self._options.mailing_address_field,
                self._options.record_id_field,
            )
            if field
        ]
        missing: list[str] = []
        for field in required:
            for row in rows:
                if field not in row or not _is_scalar_or_none(row.get(field)):
                    missing.append(field)
                    break
        return missing

    def _unusable_number_identity(
        self, rows: Sequence[Mapping[str, object]], field: str
    ) -> bool:
        for row in rows:
            raw = row.get(field)
            if raw is None:
                continue
            if parse_socrata_number_identity(raw) is None:
                return True
        return False

    def _match_outcome(
        self,
        matched: Sequence[Mapping[str, object]],
        *,
        query: PropertyLookupQuery,
        source_url: str,
        match_mode: str,
        retrieved_at: datetime,
        http_status: int,
        result_count: int,
    ) -> ProviderOutcome:
        if not matched:
            return ProviderOutcome(
                status=ProviderOutcomeStatus.NOT_FOUND,
                found=False,
                source_url=source_url,
                requires_human_review=False,
                cacheable=True,
                retryable=False,
                raw_response=self._provenance(
                    http_status=http_status,
                    result_count=0,
                    match_mode="none",
                    record_ids=(),
                    retrieved_at=retrieved_at,
                ),
            )

        if len(matched) > 1:
            evidence = []
            for row in matched:
                evidence.extend(
                    self._row_atoms(
                        row,
                        source_url=source_url,
                        retrieved_at=retrieved_at,
                        confidence=AMBIGUOUS_MATCH_CONFIDENCE,
                        review=True,
                    )
                )
            return ProviderOutcome(
                status=ProviderOutcomeStatus.SUCCESS,
                found=True,
                evidence=tuple(evidence),
                source_url=source_url,
                requires_human_review=True,
                cacheable=True,
                retryable=False,
                notes=f"Multiple matching records (n={len(matched)}); human review required.",
                raw_response=self._provenance(
                    http_status=http_status,
                    result_count=result_count,
                    match_mode="multiple",
                    record_ids=self._record_ids(matched),
                    retrieved_at=retrieved_at,
                ),
            )

        row = matched[0]
        owner_conflict = self._owner_disagrees(row, query.owner_raw_name)
        confidence = OWNER_MISMATCH_CONFIDENCE if owner_conflict else EXACT_MATCH_CONFIDENCE
        review = owner_conflict
        evidence = self._row_atoms(
            row,
            source_url=source_url,
            retrieved_at=retrieved_at,
            confidence=confidence,
            review=review,
        )
        if owner_conflict and query.owner_raw_name:
            evidence.append(
                atom(
                    field="owner_name_on_record",
                    original_value=query.owner_raw_name,
                    source=self._source_label(),
                    source_url=source_url,
                    retrieved_at=retrieved_at,
                    confidence=confidence,
                    requires_human_verification=True,
                )
            )
        return ProviderOutcome(
            status=ProviderOutcomeStatus.SUCCESS,
            found=True,
            evidence=tuple(evidence),
            source_url=source_url,
            requires_human_review=review,
            cacheable=True,
            retryable=False,
            raw_response=self._provenance(
                http_status=http_status,
                result_count=1,
                match_mode=match_mode,
                record_ids=self._record_ids(matched),
                retrieved_at=retrieved_at,
            ),
        )

    def _owner_disagrees(self, row: Mapping[str, object], query_owner: str | None) -> bool:
        if not query_owner or not query_owner.strip():
            return False
        raw = self._scalar(row.get(self._options.owner_field))
        if raw is None:
            return False
        return owner_normalize(raw) != owner_normalize(query_owner)

    def _row_atoms(
        self,
        row: Mapping[str, object],
        *,
        source_url: str,
        retrieved_at: datetime,
        confidence: float,
        review: bool,
    ) -> list[Any]:
        source = self._source_label()
        items = []
        mapping = (
            (self._options.parcel_field, "parcel_id"),
            (self._options.account_field, "account_id"),
            (self._options.owner_field, "owner_name_on_record"),
            (self._options.situs_address_field, "current_address"),
            (self._options.mailing_address_field, "mailing_address"),
            (self._options.record_id_field, "property_record_id"),
        )
        allowlist = set(self._options.select_fields)
        for src_field, dest_field in mapping:
            if not src_field or src_field not in allowlist:
                continue
            items.append(
                atom(
                    field=dest_field,
                    original_value=self._scalar(row.get(src_field)),
                    source=source,
                    source_url=source_url,
                    retrieved_at=retrieved_at,
                    confidence=confidence,
                    requires_human_verification=review,
                )
            )
        return items

    def _record_ids(self, rows: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
        field = self._options.record_id_field
        if not field:
            return ()
        ids: list[str] = []
        for row in rows:
            value = self._scalar(row.get(field))
            if value:
                ids.append(value)
        return tuple(ids)

    def _scalar(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, Decimal):
            text = format(value, "f").strip()
            return text or None
        if isinstance(value, int | float | str):
            text = str(value).strip()
            return text or None
        raise TypeError("non-scalar")

    def _source_label(self) -> str:
        return self._options.source_organization or self.name

    def _timeout(self, *, source_url: str, http_status: int | None = None) -> ProviderOutcome:
        return ProviderOutcome(
            status=ProviderOutcomeStatus.TIMEOUT,
            found=False,
            source_url=source_url,
            error_code="timeout",
            error_detail=_ERROR_DETAIL["timeout"],
            requires_human_review=True,
            cacheable=False,
            retryable=True,
            raw_response=self._provenance(
                http_status=http_status, result_count=0, retrieved_at=self._now()
            ),
        )

    def _error(
        self,
        code: str,
        *,
        source_url: str | None,
        retryable: bool,
        review: bool,
        http_status: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ProviderOutcome:
        detail = _ERROR_DETAIL.get(code, "The provider request failed.")
        provenance = self._provenance(
            http_status=http_status, result_count=0, retrieved_at=self._now()
        )
        if extra:
            provenance = {**provenance, **extra}
        return ProviderOutcome(
            status=ProviderOutcomeStatus.ERROR,
            found=False,
            source_url=source_url,
            error_code=code,
            error_detail=detail,
            requires_human_review=review,
            cacheable=False,
            retryable=retryable,
            raw_response=provenance,
        )

    def _provenance(
        self,
        *,
        http_status: int | None,
        result_count: int,
        match_mode: str | None = None,
        record_ids: tuple[str, ...] = (),
        retrieved_at: datetime | None = None,
    ) -> dict[str, Any]:
        fields_inspected = [self._options.parcel_field]
        if self._options.account_field:
            fields_inspected.append(self._options.account_field)
        when = retrieved_at or self._now()
        return {
            "provider_id": self.name,
            "source_organization": self._options.source_organization,
            "domain": self._options.domain,
            "path": self._options.path,
            "http_status": http_status,
            "retrieved_at": when.isoformat(),
            "result_count": result_count,
            "record_ids": list(record_ids),
            "match_mode": match_mode,
            "fields_inspected": fields_inspected,
        }


def _is_scalar_or_none(value: object) -> bool:
    return value is None or isinstance(value, bool | int | float | str | Decimal)
