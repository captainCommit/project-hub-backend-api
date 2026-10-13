"""What gets posted, independent of where: Slack renders it as Block Kit, Teams as an Adaptive Card.

Every message links back into the app, using the same links as notification emails.
"""

from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.services.email_delivery import app_base


@dataclass
class ChatMessage:
    # One line that also serves as the notification text on phones.
    title: str
    # Path in the app, e.g. /projects/1/sheet?task=2.
    link: str | None = None
    link_label: str = "Open in Project Hub"
    # Short paragraphs under the title.
    lines: list[str] = field(default_factory=list)
    # Label/value pairs ("Project", "Apollo").
    facts: list[tuple[str, str]] = field(default_factory=list)
    # A list of linked items, e.g. overdue tasks: (text, path).
    items: list[tuple[str, str | None]] = field(default_factory=list)
    context: str | None = None

    def url(self, settings: Settings, path: str | None) -> str | None:
        return f"{app_base(settings)}{path}" if path else None

    def plain_text(self, settings: Settings) -> str:
        parts = [self.title, *self.lines]
        parts += [f"{label}: {value}" for label, value in self.facts]
        parts += [f"• {text}" for text, _ in self.items]
        link = self.url(settings, self.link)
        if link:
            parts.append(link)
        return "\n".join(parts)


def slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_link(url: str | None, text: str) -> str:
    text = slack_escape(text).replace("|", "¦")
    return f"<{url}|{text}>" if url else text


def to_slack_blocks(message: ChatMessage, settings: Settings) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{slack_link(message.url(settings, message.link), message.title)}*"}}
    ]
    for line in message.lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": slack_escape(line)[:2900]}})
    if message.facts:
        blocks.append(
            {
                "type": "section",
                "fields": [{"type": "mrkdwn", "text": f"*{slack_escape(label)}*\n{slack_escape(value)}"} for label, value in message.facts[:10]],
            }
        )
    if message.items:
        text = "\n".join(f"• {slack_link(message.url(settings, path), item)}" for item, path in message.items)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})
    if message.link:
        blocks.append(
            {
                "type": "actions",
                "elements": [{"type": "button", "text": {"type": "plain_text", "text": message.link_label}, "url": message.url(settings, message.link)}],
            }
        )
    if message.context:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": slack_escape(message.context)}]})
    return blocks


def to_adaptive_card(message: ChatMessage, settings: Settings) -> dict[str, Any]:
    body: list[dict[str, Any]] = [{"type": "TextBlock", "text": message.title, "weight": "Bolder", "wrap": True}]
    body += [{"type": "TextBlock", "text": line, "wrap": True} for line in message.lines]
    if message.facts:
        body.append({"type": "FactSet", "facts": [{"title": label, "value": value} for label, value in message.facts]})
    for item, path in message.items:
        url = message.url(settings, path)
        body.append({"type": "TextBlock", "text": f"- [{item}]({url})" if url else f"- {item}", "wrap": True, "spacing": "Small"})
    if message.context:
        body.append({"type": "TextBlock", "text": message.context, "isSubtle": True, "size": "Small", "wrap": True})
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if message.link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": message.link_label, "url": message.url(settings, message.link)}]
    return card
