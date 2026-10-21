"""Exam matching and local watch preferences. Standard library only."""

import calendar
import html
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

ALL_EXAMS = "All board exams"
# PRC profession names: https://www.prc.gov.ph/2026-schedule-examination
EXAMS = {
    "Aeronautical Engineers": r"aeronautical engineer(?:s|ing)?",
    "Agricultural and Biosystems Engineers": r"agricultural (?:and biosystems )?engineer(?:s|ing)?",
    "Agriculturists": r"agriculturists?|agriculture",
    "Architects": r"(?<!landscape )(?<!naval )(?:architects?|architecture)",
    "Certified Plant Mechanics": r"(?:certified )?plant mechanics?",
    "Certified Public Accountants": r"(?:certified public )?accountants?|accountancy|cpale",
    "Chemical Engineers": r"chemical engineer(?:s|ing)?",
    "Chemical Technicians": r"chemical technicians?",
    "Chemists": r"chemists?|chemistry",
    "Civil Engineers": r"civil engineer(?:s|ing)?",
    "Criminologists": r"criminologists?|criminology",
    "Customs Brokers": r"customs brokers?",
    "Dental Hygienists": r"dental hygienists?",
    "Dental Technologists": r"dental technologists?",
    "Dentists": r"dentists?|dentistry",
    "Electronics Engineers": r"(?<!professional )electronics engineer(?:s|ing)?",
    "Electronics Technicians": r"electronics technicians?",
    "Environmental Planners": r"environmental (?:planners?|planning)",
    "Fisheries Professionals": r"fisheries (?:professionals?|technologists?)",
    "Food Technologists": r"(?:professional )?food (?:technologists?|technology)",
    "Foresters": r"foresters?|forestry",
    "Geodetic Engineers": r"geodetic engineer(?:s|ing)?",
    "Geologists": r"geologists?|geology",
    "Guidance Counselors": r"guidance counsel(?:ors?|ing)",
    "Interior Designers": r"interior design(?:ers?)?",
    "Landscape Architects": r"landscape (?:architects?|architecture)",
    "Librarians": r"librarians?",
    "Master Plumbers": r"(?:master )?plumbers?|plumbing",
    "Mechanical Engineers": r"(?<!professional )mechanical engineer(?:s|ing)?",
    "Medical Technologists": r"medical (?:technologists?|technology)",
    "Metallurgical Engineers": r"metallurgical engineer(?:s|ing)?",
    "Midwives": r"midwives|midwife|midwifery",
    "Mining Engineers": r"mining engineer(?:s|ing)?",
    "Naval Architects": r"naval (?:architects?|architecture)",
    "Nurses": r"nurses?|nursing|pnle",
    "Nutritionist-Dietitians": r"nutritionists?(?: dietitians?)?|nutrition and dietetics",
    "Occupational Therapists": r"occupational therap(?:ists?|y)",
    "Optometrists": r"optometrists?|optometry",
    "Pharmacists": r"pharmacists?|pharmacy|phle",
    "Physical Therapists": r"physical therap(?:ists?|y)",
    "Physicians": r"physicians?",
    "Professional Electrical Engineers": r"professional electrical engineers?",
    "Professional Electronics Engineers": r"professional electronics engineers?",
    "Professional Mechanical Engineers": r"professional mechanical engineers?",
    "Professional Teachers": r"(?:professional )?teachers?|lept|let",
    "Psychologists": r"psychologists?|psychology",
    "Psychometricians": r"psychometricians?",
    "Radiologic Technologists": r"radiologic(?:al)? (?:technologists?|technology)",
    "Real Estate Appraisers": r"real estate appraisers?",
    "Real Estate Brokers": r"real estate brokers?",
    "Real Estate Consultants": r"real estate consultants?",
    "Registered Electrical Engineers": r"(?<!professional )(?:registered )?electrical engineer(?:s|ing)?",
    "Registered Master Electricians": r"(?:registered )?master electricians?",
    "Respiratory Therapists": r"respiratory therap(?:ists?|y)",
    "Sanitary Engineers": r"sanitary engineer(?:s|ing)?",
    "Social Workers": r"social workers?",
    "Speech-Language Pathologists": r"speech language patholog(?:ists?|y)",
    "Veterinarians": r"veterinarians?|veterinary medicine",
    "X-Ray Technologists": r"x ray (?:technologists?|technology)",
}
EXCLUDED_NOTICE = (
    r"\b(?:not yet|not released|will be|expected|release date|schedule|"
    r"rescheduled|postponed|delay|delayed|oathtaking|oath taking|"
    r"topnotchers|top performing|performance of schools|room assignment)\b"
)
MONTHS = "|".join(name.lower() for name in calendar.month_name if name)


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(value).lower()).strip()


@dataclass(frozen=True)
class Target:
    month: str = "October"
    year: str = "2026"
    exam: str = "Pharmacists"

    @property
    def is_all(self) -> bool:
        return self.exam == ALL_EXAMS

    @property
    def period(self) -> str:
        return " ".join(part for part in (self.month, self.year) if part) or "Any date"

    def validate(self):
        if self.month not in ("", *calendar.month_name[1:]):
            raise ValueError("Choose an exam month or Any month.")
        if self.year and not re.fullmatch(r"20\d{2}", self.year):
            raise ValueError("Enter a year from 2000 to 2099, or leave it blank.")
        if not 2 <= len(normalize(self.exam)) <= 100:
            raise ValueError("Choose an exam or type its full name.")

    def matches(self, value: str) -> bool:
        text = normalize(value)
        if re.search(EXCLUDED_NOTICE, text):
            return False
        exam_context = re.search(r"\b(?:licensure|board|examinations?|phle|lept|pnle|cpale)\b", text)
        technical_evaluation = ("technical evaluation" in text and re.search(
            r"\bprofessional (?:electrical|mechanical|electronics) engineers?\b", text))
        if not (re.search(r"\bresults?\b", text) and (exam_context or technical_evaluation)):
            return False
        if self.month and self.year:
            cycle = (rf"\b{re.escape(self.month.lower())}"
                     rf"(?:\s+(?:{MONTHS}|and|to|through|or))*\s+{re.escape(self.year)}\b")
        else:
            cycle = rf"\b{re.escape((self.month or self.year).lower())}\b"
        if (self.month or self.year) and not re.search(cycle, text):
            return False
        pattern = EXAMS.get(self.exam, re.escape(normalize(self.exam)))
        return self.is_all or bool(re.search(rf"\b(?:{pattern})\b", text))


@dataclass(frozen=True)
class Watch:
    target: Target = Target()
    sound: str = ""
    new_only: bool = False


def load_watches(path: Path) -> list[Watch]:
    if not path.exists():
        return [Watch()]
    try:
        if path.stat().st_size > 256 * 1024:
            raise ValueError("Watch list is too large.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Unrecognized watch-list format.")
        rows = data.get("watches")
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("Watch list must contain at most 100 entries.")
        watches: list[Watch] = []
        for row in rows:
            target = Target(**row["target"])
            if not all(isinstance(value, str) for value in asdict(target).values()):
                raise ValueError("Exam and date values must be text.")
            target.validate()
            sound, new_only = row.get("sound", ""), row.get("new_only", False)
            if not isinstance(sound, str) or len(sound) > 4096 or "\0" in sound:
                raise ValueError("Invalid sound path.")
            if sound and Path(sound).suffix.lower() != ".wav":
                raise ValueError("Custom sounds must be WAV files.")
            if not isinstance(new_only, bool):
                raise ValueError("Invalid new-announcements setting.")
            watch = Watch(target, sound, new_only)
            if any(item.target == target for item in watches):
                raise ValueError("Watch list contains a duplicate exam and date.")
            watches.append(watch)
        return watches
    except (KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise ValueError("Watch list has invalid entries.") from exc


def save_watches(path: Path, watches: list[Watch]):
    data = {"version": 1, "watches": [asdict(watch) for watch in watches]}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".watchlist-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
