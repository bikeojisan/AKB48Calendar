import re
import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright


BASE_URL = "https://www.akb48.co.jp"
SCHEDULE_URL = f"{BASE_URL}/about/schedule"

KEYWORDS = (
    "握手会",
    "写真会",
    "オンラインお話し会",
    "コンサート",
    "LIVE",
    "ライブ",
    "TOUR",
    "ツアー",
)

MONTHS_AHEAD = 18


def add_months(d, months):
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    return date(year, month, 1)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_date_text(value):
    if value is None:
        return None

    text = str(value)

    m = re.search(
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})",
        text,
    )

    if not m:
        return None

    try:
        return date(
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
        )
    except ValueError:
        return None


def parse_datetime_text(value, jst):
    if value is None:
        return None

    text = str(value).strip()

    try:
        dt = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=jst)

        return dt.astimezone(jst)

    except ValueError:
        pass

    m = re.search(
        r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})"
        r"(?:[T\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?",
        text,
    )

    if not m:
        return None

    try:
        return datetime(
            int(m.group(1)),
            int(m.group(2)),
            int(m.group(3)),
            int(m.group(4) or 0),
            int(m.group(5) or 0),
            int(m.group(6) or 0),
            tzinfo=jst,
        )
    except ValueError:
        return None


def is_target_title(title):
    title = title.casefold()

    return any(
        keyword.casefold() in title
        for keyword in KEYWORDS
    )


def collect_schedule_items(obj, out):
    if isinstance(obj, dict):

        title = clean_text(
            obj.get("title")
        )

        event_date = parse_date_text(
            obj.get("date")
        )

        if (
            title
            and event_date
            and is_target_title(title)
        ):
            out.append(obj)

        for value in obj.values():
            collect_schedule_items(
                value,
                out,
            )

    elif isinstance(obj, list):

        for value in obj:
            collect_schedule_items(
                value,
                out,
            )


def get_item_id(item, title, event_date):
    for key in (
        "id",
        "schedule_id",
        "scheduleId",
    ):
        value = item.get(key)

        if value not in (None, ""):
            return str(value)

    raw = (
        f"{event_date.isoformat()}|"
        f"{title}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:20]


def get_location(item, title):

    for key in (
        "place",
        "location",
        "venue",
    ):
        value = clean_text(
            item.get(key)
        )

        if value:
            return value

    if "オンライン" in title:
        return "オンライン"

    m = re.search(
        r"[@＠]\s*(.+)$",
        title,
    )

    if m:
        return clean_text(
            m.group(1)
        )

    return ""


def main():

    jst = ZoneInfo(
        "Asia/Tokyo"
    )

    today = datetime.now(
        jst
    ).date()

    this_month = date(
        today.year,
        today.month,
        1,
    )

    raw_items = []
    json_urls = set()

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )

        def handle_response(response):

            # SCHEDULEが裏で取得した
            # JSONデータを見る
            if response.request.resource_type not in (
                "xhr",
                "fetch",
            ):
                return

            try:
                data = response.json()
            except Exception:
                return

            json_urls.add(
                response.url
            )

            collect_schedule_items(
                data,
                raw_items,
            )

        page.on(
            "response",
            handle_response,
        )

        for i in range(
            MONTHS_AHEAD
        ):

            month = add_months(
                this_month,
                i,
            )

            url = (
                f"{SCHEDULE_URL}"
                f"?date={month.isoformat()}"
            )

            print(
                "確認:",
                url,
            )

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            page.wait_for_timeout(
                3000
            )

        browser.close()

    print(
        "SCHEDULEデータ取得先:",
        len(json_urls),
    )

    events_by_key = {}

    for item in raw_items:

        title = clean_text(
            item.get("title")
        )

        event_date = parse_date_text(
            item.get("date")
        )

        if not title or not event_date:
            continue

        # 過去はAppleカレンダーに入れない
        if event_date < today:
            continue

        item_id = get_item_id(
            item,
            title,
            event_date,
        )

        key = (
            f"{item_id}|"
            f"{event_date.isoformat()}"
        )

        events_by_key[key] = {
            "id": item_id,
            "title": title,
            "date": event_date,
            "start": item.get("date"),
            "end": item.get("end_date"),
            "location": get_location(
                item,
                title,
            ),
        }

    events = sorted(
        events_by_key.values(),
        key=lambda x: (
            x["date"],
            x["title"],
        ),
    )

    print(
        "今日以降の対象予定:",
        len(events),
    )

    cal = Calendar()

    cal.add(
        "prodid",
        "-//AKB48 Schedule Calendar//JA",
    )

    cal.add(
        "version",
        "2.0",
    )

    cal.add(
        "x-wr-calname",
        "AKB48 コンサート・握手会・写真会・オンラインお話し会",
    )

    cal.add(
        "x-wr-timezone",
        "Asia/Tokyo",
    )

    for item in events:

        ev = Event()

        ev.add(
            "uid",
            (
                f"akb48-"
                f"{item['id']}-"
                f"{item['date'].isoformat()}"
                f"@schedule"
            ),
        )

        ev.add(
            "summary",
            item["title"],
        )

        start = parse_datetime_text(
            item["start"],
            jst,
        )

        end = parse_datetime_text(
            item["end"],
            jst,
        )

        has_time = (
            start is not None
            and (
                start.hour != 0
                or start.minute != 0
            )
        )

        if has_time:

            ev.add(
                "dtstart",
                start,
            )

            if end and end > start:
                ev.add(
                    "dtend",
                    end,
                )
            else:
                ev.add(
                    "dtend",
                    start
                    + timedelta(hours=1),
                )

        else:

            ev.add(
                "dtstart",
                item["date"],
            )

            ev.add(
                "dtend",
                item["date"]
                + timedelta(days=1),
            )

        if item["location"]:
            ev.add(
                "location",
                item["location"],
            )

        ev.add(
            "description",
            "AKB48公式SCHEDULEから自動取得",
        )

        cal.add_component(ev)

    # 0件でも正常に作る
    with open(
        "calendar.ics",
        "wb",
    ) as f:

        f.write(
            cal.to_ical()
        )

    print(
        "calendar.ics作成完了:",
        len(events),
        "件",
    )


if __name__ == "__main__":
    main()
