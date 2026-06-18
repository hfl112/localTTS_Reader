from pydantic import BaseModel, Field


class ReadRequest(BaseModel):
    text: str = ""
    voice: str | None = None
    source: str | None = None
    from_saved: bool = False
    performance_profile: str | None = None


class SeekRequest(BaseModel):
    direction: int = 1


class ReadUrlRequest(BaseModel):
    url: str = ""
    html: str = ""
    translate: bool = False
    mode: str = "original"
    save: bool = False
    podcast: bool = False

    def effective_mode(self) -> str:
        if self.mode == "original" and self.translate:
            return "translate"
        return self.mode

    def action(self) -> str:
        if self.podcast:
            return "podcast"
        if self.save:
            return "save"
        return "read"


class DeleteSavedRequest(BaseModel):
    md5: str | None = None
    index: int | None = None


class FilenameRequest(BaseModel):
    filename: str = ""


class SaveForLaterRequest(BaseModel):
    text: str = ""
    source: str = "web"
    voice: str | None = None
    title: str | None = None


class GenerateSinglePodcastRequest(BaseModel):
    text: str = ""
    source: str = "web"
    voice: str | None = None
    title: str | None = None
    performance_profile: str = "quiet"


class PlaySavedRequest(BaseModel):
    indices: list[int] = Field(default_factory=list)


class Md5Request(BaseModel):
    md5: str | None = None
