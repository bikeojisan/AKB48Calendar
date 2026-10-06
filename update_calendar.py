import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse, parse_qs
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event
from playwright.sync_api import sync_playwright


BASE_URL = "https://www.akb48.co.jp"
SCHEDULE_URL = f"{BASE_URL}/about/schedule"

# この3種類だけAppleカレンダーに入れる
KEYWORDS = (
    "握手会",
    "写真会",
    "オンラインお話し会",
)

# 今月から何か月先まで確認するか
MONTHS_AHEAD = 18


def add_months(d, months):
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    return date(year, month, 1)


def event_id_from_url(url):
    query = parse_qs(urlparse(url).query)
    return query.get("id", [url])[0]


def find_event_date(text, target_month):
    # 例：2027年1月9日
    m = re.search(
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
        text
    )

    if m:
        try:
            return date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3))
            )
        except ValueError:
            pass

    # 例：1/9
    matches = re.finditer(
        r"(?<!\d)(\d{1,2})/(\d{1,2})(?!\d)",
        text
    )

    for m in matches:
        month = int(m.group(1))
        day = int(m.group(2))

        if month == target_month.month:
            try:
                return date(target_month.year, month, day)
            except ValueError:
                pass

    return None


def find_time(text):
    # 例：10:30～19:15
    m = re.search(
        r"([01]?\d|2[0-3]):([0-5]\d)"
        r"\s*(?:-|–|—|〜|～|~)\s*"
        r"([01]?\d|2[0-3]):([0-5]\d)",
        text
    )

    if not m:
        return None

    return (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        int(m.group(4)),
    )


def find_location(title):
    if "オンライン" in title:
        return "オンライン"

    # 「@幕張メッセ」など
    m = re.search(r"[@＠]\s*(.+)$", title)

    if m:
        return m.group(1).strip()

    return ""


def main():

    jst = ZoneInfo("Asia/Tokyo")
    today = datetime.now(jst).date()

    first_month = date(
        today.year,
        today.month,
        1
    )

    found = {}

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            locale="ja-JP",
            timezone_id="Asia/Tokyo"
        )

        # -------------------------
        # 公式SCHEDULEを18か月確認
        # -------------------------

        for i in range(MONTHS_AHEAD):

            month = add_months(first_month, i)

            url = (
                f"{SCHEDULE_URL}"
                f"?date={month.isoformat()}"
            )

            print("確認:", url)

            page.goto(
                url,
                wait_until="domcontentloaded"
            )

            page.wait_for_timeout(2500)

            links = page.locator("a").evaluate_all(
                """
                elements => elements.map(a => ({
                    href: a.href || "",
                    text: (
                        a.innerText ||
                        a.textContent ||
                        ""
                    ).trim()
                }))
                .filter(x =>
                    x.href.includes("/about/schedule")
                    &&
                    x.href.includes("id=")
                )
                """
            )

            for item in links:

                title = re.sub(
                    r"\s+",
                    " ",
                    item["text"]
                ).strip()

                if not title:
                    continue

                # 対象イベントだけ残す
                if not any(
                    keyword in title
                    for keyword in KEYWORDS
                ):
                    continue

                href = urljoin(
                    BASE_URL,
                    item["href"]
                )

                eid = event_id_from_url(href)

                # 同じschedule IDは1件だけ
                if eid not in found:

                    found[eid] = {
                        "id": eid,
                        "title": title,
                        "url": href,
                        "month": month,
                    }

        print(
            "対象イベント候補:",
            len(found)
        )

        if not found:
            raise RuntimeError(
                "対象イベントが0件でした。"
                "calendar.icsを上書きしません。"
            )

        # -------------------------
        # 各イベント詳細を読む
        # -------------------------

        events = []

        detail = browser.new_page(
            locale="ja-JP",
            timezone_id="Asia/Tokyo"
        )

        for item in found.values():

            print(
                "詳細確認:",
                item["title"]
            )

            detail.goto(
                item["url"],
                wait_until="domcontentloaded"
            )

            detail.wait_for_timeout(2000)

            text = detail.locator(
                "body"
            ).inner_text()

            event_date = find_event_date(
                text,
                item["month"]
            )

            if not event_date:
                print(
                    "日付取得失敗:",
                    item["url"]
                )
                continue

            time_data = find_time(text)

            events.append({
                **item,
                "date": event_date,
                "time": time_data,
                "location": find_location(
                    item["title"]
                ),
            })

        browser.close()

    # -------------------------
    # Appleカレンダー用ICS
    # -------------------------

    cal = Calendar()

    cal.add(
        "prodid",
        "-//AKB48 Schedule Calendar//JA"
    )

    cal.add(
        "version",
        "2.0"
    )

    cal.add(
        "x-wr-calname",
        "AKB48 握手会・写真会・オンラインお話し会"
    )

    cal.add(
        "x-wr-timezone",
        "Asia/Tokyo"
    )

    for item in sorted(
        events,
        key=lambda x: x["date"]
    ):

        ev = Event()

        ev.add(
            "uid",
            f"akb48-{item['id']}@schedule"
        )

        ev.add(
            "summary",
            item["title"]
        )

        # 毎回変化しないようイベント日を使用
        ev.add(
            "dtstamp",
            datetime(
                item["date"].year,
                item["date"].month,
                item["date"].day,
                tzinfo=timezone.utc
            )
        )

        if item["time"]:

            sh, sm, eh, em = item["time"]

            start = datetime(
                item["date"].year,
                item["date"].month,
                item["date"].day,
                sh,
                sm,
                tzinfo=jst
            )

            end = datetime(
                item["date"].year,
                item["date"].month,
                item["date"].day,
                eh,
                em,
                tzinfo=jst
            )

            ev.add("dtstart", start)
            ev.add("dtend", end)

        else:

            # 時間が取れない場合は終日予定
            ev.add(
                "dtstart",
                item["date"]
            )

            ev.add(
                "dtend",
                item["date"]
                + timedelta(days=1)
            )

        if item["location"]:
            ev.add(
                "location",
                item["location"]
            )

        ev.add(
            "description",
            "AKB48公式SCHEDULEから自動取得\n"
            + item["url"]
        )

        ev.add(
            "url",
            item["url"]
        )

        cal.add_component(ev)

    with open(
        "calendar.ics",
        "wb"
    ) as f:

        f.write(
            cal.to_ical()
        )

    print(
        "calendar.ics作成完了:",
        len(events),
        "件"
    )


if __name__ == "__main__":
    main()
