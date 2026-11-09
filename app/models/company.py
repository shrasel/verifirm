from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class OperatingStatus(str, Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    DISSOLVED = "Dissolved"
    ACQUIRED = "Acquired"
    UNKNOWN = "Unknown"


class HeadquartersAddress(BaseModel):
    street: Optional[str] = Field(default=None, description="Street address including building/suite")
    city: Optional[str] = Field(default=None, description="City or municipality")
    state: Optional[str] = Field(default=None, description="State, province, or region code")
    postal_code: Optional[str] = Field(default=None, description="Postal / ZIP code")
    country: Optional[str] = Field(default=None, description="Country name or ISO-3166 code")
    formatted_address: Optional[str] = Field(default=None, description="Complete single-line address string")


class IndustryTaxonomy(BaseModel):
    sector: Optional[str] = Field(default=None, description="High-level industry sector")
    naics_code: Optional[str] = Field(default=None, description="6-digit NAICS classification code")
    sic_code: Optional[str] = Field(default=None, description="4-digit Standard Industrial Classification (SIC) code")
    label: Optional[str] = Field(default=None, description="Standardized industry descriptive label")


class SecurityListing(BaseModel):
    """Represents a publicly traded security instrument distinct from the legal entity."""
    ticker: str = Field(..., description="Listed security ticker symbol")
    exchange: Optional[str] = Field(default=None, description="Exchange code (e.g., NASDAQ, NYSE)")
    cik: Optional[str] = Field(default=None, description="Associated SEC CIK")
    figi: Optional[str] = Field(default=None, description="OpenFIGI instrument identifier")


class LegalEntityRecord(BaseModel):
    """
    Distinct representation of the statutory legal entity as recorded in an official registry.
    Never collapses trading brand or website liveness into legal status.
    """
    registered_name: Optional[str] = Field(default=None, description="Exact official registered name without casing alteration")
    jurisdiction: Optional[str] = Field(default=None, description="Jurisdiction of incorporation (e.g., 'US-DE', 'GB')")
    registration_number: Optional[str] = Field(default=None, description="Official company registration number")
    lei: Optional[str] = Field(default=None, description="20-character ISO 17442 Legal Entity Identifier")
    cik: Optional[str] = Field(default=None, description="US SEC Central Index Key")
    legal_status: OperatingStatus = Field(default=OperatingStatus.UNKNOWN, description="Statutory legal status from registry")
    incorporation_date: Optional[str] = Field(default=None, description="Date or year of statutory incorporation")
    registered_office: Optional[HeadquartersAddress] = Field(default=None, description="Statutory registered office / mailing address")
    former_names: list[str] = Field(default_factory=list, description="Historical legal names from registry filings")
    parent_lei: Optional[str] = Field(default=None, description="Direct or ultimate parent LEI if disclosed")


class BrandProfile(BaseModel):
    """
    Public-facing commercial brand or operating profile.
    """
    brand_name: Optional[str] = Field(default=None, description="Trading or commercial brand name")
    operating_headquarters: Optional[HeadquartersAddress] = Field(default=None, description="Principal executive / operating headquarters")
    phone: Optional[str] = Field(default=None, description="Primary verified contact telephone")
    email: Optional[str] = Field(default=None, description="Primary verified contact email")
    employee_range: Optional[str] = Field(default=None, description="Employee headcount range")
    description: Optional[str] = Field(default=None, description="Business description")


class WebsiteAssertion(BaseModel):
    """
    Treats the web domain as a separate assertion linked to the company by explicit evidence,
    rather than assuming the domain itself is the company's canonical identity.
    """
    canonical_domain: Optional[str] = Field(default=None, description="Normalized root domain")
    website_url: Optional[str] = Field(default=None, description="Canonical URL")
    is_online: bool = Field(default=False, description="Whether the website responded to HTTP probing")
    http_status: Optional[int] = Field(default=None, description="HTTP status code from probe")
    linkage_signals: list[str] = Field(
        default_factory=list,
        description="Signals linking the domain to the legal entity (e.g., terms_legal_name_match, address_match, phone_match)"
    )
    verification_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Evidence score linking domain to legal entity")
    is_independently_verified: bool = Field(
        default=False,
        description="True when corroborated by legal terms, registry link, or matching registry address/phone"
    )


class CompanyData(BaseModel):
    legal_name: Optional[str] = Field(
        default=None,
        description="Official registered legal entity name (exact spelling preserved)"
    )
    brand_name: Optional[str] = Field(
        default=None,
        description="Public-facing trade or brand name (e.g., 'Stripe')"
    )
    status: OperatingStatus = Field(
        default=OperatingStatus.UNKNOWN,
        description="Statutory legal entity status (defaults to Unknown unless verified by registry/terms)"
    )
    founded_year: Optional[int] = Field(
        default=None,
        description="Year of earliest verifiable incorporation or launch"
    )
    headquarters: Optional[HeadquartersAddress] = Field(
        default=None,
        description="Primary operating headquarters or principal executive office"
    )
    registered_office: Optional[HeadquartersAddress] = Field(
        default=None,
        description="Statutory registered office address (kept distinct from operating headquarters)"
    )
    employee_range: Optional[str] = Field(
        default=None,
        description="Standardized employee headcount range (e.g., '1-10', '11-50', '5001-10000')"
    )
    industry: Optional[IndustryTaxonomy] = Field(
        default=None,
        description="Industry classification and taxonomy"
    )
    website: Optional[str] = Field(
        default=None,
        description="Canonical primary website URL"
    )
    description: Optional[str] = Field(
        default=None,
        description="Concise, verified description of the company's business"
    )
    phone: Optional[str] = Field(
        default=None,
        description="Primary verified phone number (e.g., '+1 800-555-0199')"
    )
    email: Optional[str] = Field(
        default=None,
        description="Primary verified contact or general email address"
    )
    cik: Optional[str] = Field(
        default=None,
        description="US SEC Central Index Key (if public/reporting company)"
    )
    lei: Optional[str] = Field(
        default=None,
        description="20-character ISO 17442 Legal Entity Identifier"
    )
    registration_number: Optional[str] = Field(
        default=None,
        description="Jurisdiction company registration number"
    )
    jurisdiction: Optional[str] = Field(
        default=None,
        description="Statutory jurisdiction of incorporation (e.g., 'US-DE', 'GB')"
    )
    active_web_presence: bool = Field(
        default=False,
        description="Indicates whether the entity possesses a live, responsive web domain (distinct from legal status)"
    )
    # Distinct structured records separating Legal Entity, Brand, Website Assertion, and Listed Securities
    legal_entity: Optional[LegalEntityRecord] = Field(
        default=None,
        description="Distinct statutory legal entity record"
    )
    brand_profile: Optional[BrandProfile] = Field(
        default=None,
        description="Distinct commercial brand and operating profile"
    )
    website_assertion: Optional[WebsiteAssertion] = Field(
        default=None,
        description="Explicit domain-to-entity linkage assertion and verification signals"
    )
    securities: list[SecurityListing] = Field(
        default_factory=list,
        description="Associated listed securities (tickers, exchanges, CIK/FIGI)"
    )
