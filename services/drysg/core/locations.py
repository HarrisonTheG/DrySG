"""Location records shared by ingestion, postal lookup and persistence."""

from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
POSTAL_CODE_PATTERN = r"^[0-9]{6}$"
PostalCode = Annotated[str, Field(pattern=POSTAL_CODE_PATTERN)]


class Coordinates(BaseModel):
    """Validated coordinates in decimal degrees."""

    model_config = ConfigDict(frozen=True)
    latitude: Latitude
    longitude: Longitude


class AreaLocation(Coordinates):
    """NEA forecast area label point used for nearest-area lookup."""

    area: str = Field(min_length=1)


class PostalAddress(Coordinates):
    """An exact postal-code match returned by OneMap."""

    postal_code: PostalCode
    address: str = Field(min_length=1)


class PostalLocation(PostalAddress):
    """Resolved location stored in the PostalCache table."""

    area: str = Field(min_length=1)
    cached_at: AwareDatetime
