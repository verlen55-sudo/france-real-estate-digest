"""
Еженедельный дайджест новостей о недвижимости во Франции.

Собирает три блока новостей за последние 7 дней:
1. Юридические новости о недвижимости (резерв: Google News RSS — у ANIL
   и service-public.fr нет настоящего RSS-фида, см. README).
2. Рынок недвижимости (резерв: Google News RSS — у нотариусов
   (notaires.fr / statistiques.notaires.fr) нет RSS-фида с данными о
   ценах/рынке, см. README).
3. Новости Монтона и Лазурного берега (необязательный блок).

и отправляет письмо на почту через Gmail SMTP.
"""

import argparse
import html
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser

# --- Источники ------------------------------------------------------------
# Все три фида — Google News RSS (резервный вариант для блоков 1 и 2,
# прямой вариант по заданию для блока 3). Подробности — в README.md.

LEGAL_FEED_URL = "https://news.google.com/rss/search?q=%22droit%20immobilier%22%20France&hl=fr&gl=FR"
MARKET_FEED_URL = (
    "https://news.google.com/rss/search?q=%22prix%20immobilier%22%20OR%20"
    "%22march%C3%A9%20immobilier%22%20France&hl=fr&gl=FR"
)
MENTON_FEED_URL = "https://news.google.com/rss/search?q=Menton&hl=fr&gl=FR"

DAYS_BACK = 7
MAX_ITEMS_PER_SECTION = 15


def fetch_recent_entries(feed_url, days_back=DAYS_BACK):
    """Скачивает RSS-фид и возвращает записи за последние days_back дней."""
    parsed = feedparser.parse(feed_url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    entries = []
    seen_links = set()
    for entry in parsed.entries:
        published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
        if not published_struct:
            continue  # без даты нельзя проверить "за последние 7 дней" — пропускаем
        published_dt = datetime.fromtimestamp(time.mktime(published_struct), tz=timezone.utc)
        if published_dt < cutoff:
            continue

        link = entry.get("link", "")
        if link in seen_links:
            continue
        seen_links.add(link)

        source = _extract_source(entry)
        title = _clean_title(entry.get("title", ""), source)

        entries.append({
            "title": title,
            "link": link,
            "source": source,
            "published": published_dt,
        })

    entries.sort(key=lambda e: e["published"], reverse=True)
    return entries[:MAX_ITEMS_PER_SECTION]


def _extract_source(entry):
    """Google News кладёт имя источника в entry.source.title, если есть."""
    source = getattr(entry, "source", None)
    if source is not None:
        title = getattr(source, "title", None)
        if title:
            return title
    # запасной вариант: Google News часто пишет "Заголовок - Источник"
    title = entry.get("title", "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return ""


def _clean_title(title, source):
    """Убирает ' - Источник' из заголовка, если он там есть."""
    if source and title.endswith(" - " + source):
        return title[: -len(" - " + source)].strip()
    return title.strip()


def fetch_all_sections():
    """Возвращает словарь {название блока: список новостей}."""
    sections = {}
    for name, url in (
        ("legal", LEGAL_FEED_URL),
        ("market", MARKET_FEED_URL),
        ("menton", MENTON_FEED_URL),
    ):
        try:
            sections[name] = fetch_recent_entries(url)
        except Exception as exc:  # не даём одному сбойному фиду сломать всё письмо
            print(f"Предупреждение: не удалось получить блок '{name}': {exc}", file=sys.stderr)
            sections[name] = []
    return sections


SECTION_TITLES = {
    "legal": "Юридические новости о недвижимости во Франции",
    "market": "Рынок недвижимости Франции",
    "menton": "Монтон и Лазурный берег",
}


def build_email(sections, now=None):
    """Собирает заголовок письма, текстовую и HTML-версии."""
    now = now or datetime.now()
    week_ago = now - timedelta(days=DAYS_BACK)
    subject = (
        f"Дайджест недвижимости Франции — неделя с "
        f"{week_ago.strftime('%d.%m')} по {now.strftime('%d.%m')}"
    )

    text_parts = [subject, ""]
    html_parts = [
        "<html><body style='font-family: Arial, sans-serif; font-size: 14px;'>",
        f"<h2>{html.escape(subject)}</h2>",
    ]

    any_section_included = False
    for key in ("legal", "market", "menton"):
        items = sections.get(key, [])
        if not items:
            continue  # необязательный блок Монтона (и любой пустой блок) просто не выводится
        any_section_included = True

        title = SECTION_TITLES[key]
        text_parts.append(title)
        text_parts.append("-" * len(title))
        html_parts.append(f"<h3>{html.escape(title)}</h3><ul>")

        for item in items:
            source_suffix = f" — {item['source']}" if item["source"] else ""
            text_parts.append(f"- {item['title']}{source_suffix}\n  {item['link']}")
            html_parts.append(
                "<li><a href=\"{link}\">{title}</a>{source}</li>".format(
                    link=html.escape(item["link"]),
                    title=html.escape(item["title"]),
                    source=html.escape(source_suffix),
                )
            )
        text_parts.append("")
        html_parts.append("</ul>")

    if not any_section_included:
        text_parts.append("За последнюю неделю новостей не найдено.")
        html_parts.append("<p>За последнюю неделю новостей не найдено.</p>")

    html_parts.append("</body></html>")

    return subject, "\n".join(text_parts), "\n".join(html_parts)


def send_email(subject, text_body, html_body):
    gmail_user = os.environ["GMAIL_USER"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("RECIPIENT_EMAIL", "verlen55@gmail.com")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = gmail_user
    message["To"] = recipient
    message.attach(MIMEText(text_body, "plain", "utf-8"))
    message.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, recipient, message.as_string())


def main():
    parser = argparse.ArgumentParser(description="Еженедельный дайджест недвижимости Франции")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только собрать и показать письмо в консоли, не отправлять",
    )
    args = parser.parse_args()

    sections = fetch_all_sections()
    subject, text_body, html_body = build_email(sections)

    if args.dry_run:
        print(subject)
        print()
        print(text_body)
        return

    send_email(subject, text_body, html_body)
    print("Письмо отправлено.")


if __name__ == "__main__":
    main()
