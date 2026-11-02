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


class CompanyData(BaseModel):
    legal_name: Optional[str] = Field(
        default=None,
        description="Official registered legal entity name (e.g., 'Stripe, Inc.')"
    )
    brand_name: Optional[str] = Field(
        default=None,
        description="Public-facing trade or brand name (e.g., 'Stripe')"
    )
    status: OperatingStatus = Field(
        default=OperatingStatus.ACTIVE,
        description="Current operational status verified via registry or live presence"
    )
    founded_year: Optional[int] = Field(
        default=None,
        description="Year of earliest verifiable incorporation or launch"
    )
    headquarters: Optional[HeadquartersAddress] = Field(
        default=None,
        description="Primary headquarters or principal executive office"
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
    cik: Optional[str] = Field(
        default=None,
        description="US SEC Central Index Key (if public/reporting company)"
    )
    active_web_presence: bool = Field(
        default=True,
        description="Indicates whether the entity possesses a live, responsive web domain"
    )
