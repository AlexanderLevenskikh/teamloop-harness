"""
TeamLoop Harness — Inbox Module
Lightweight per-run inbox for agent-to-agent event-driven notifications.

Messages are scoped to a single run (no persistent cross-run messaging).
Stored in .teamloop/runs/<run-id>/inbox.jsonl as JSONL.
"""
import datetime
import json
import os


def _inbox_path(workspace, run_id):
    """Return the full path to inbox.jsonl for a given run."""
    return os.path.join(workspace, "runs", run_id, "inbox.jsonl")


def _read_inbox(workspace, run_id):
    """Read all messages from inbox.jsonl."""
    path = _inbox_path(workspace, run_id)
    if not os.path.exists(path):
        return []
    entries = []
    for enc in ("utf-8-sig", "utf-8"):
        try:
            with open(path, "r", encoding=enc) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
            return entries
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return entries


def _write_inbox(workspace, run_id, messages):
    """Overwrite inbox.jsonl with the given list of messages."""
    path = _inbox_path(workspace, run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _next_message_id(messages):
    """Determine the next messageId counter based on existing messages."""
    counter = 1
    for msg in messages:
        mid = msg.get("messageId", "")
        if mid.startswith("msg-"):
            try:
                num = int(mid[4:])
                if num >= counter:
                    counter = num + 1
            except ValueError:
                pass
    return counter


def inbox_send(workspace, run_id, from_actor, to_actor, subject, body):
    """Append a message to the run's inbox.jsonl.

    Parameters
    ----------
    workspace : str
        Absolute path to the .teamloop workspace.
    run_id : str
        Run identifier (e.g. run-20260712...).
    from_actor : str
        Sender actor name.
    to_actor : str
        Recipient actor name.
    subject : str
        Message subject line.
    body : str
        Message body text.

    Returns
    -------
    dict
        The message that was appended.
    """
    messages = _read_inbox(workspace, run_id)
    counter = _next_message_id(messages)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    message = {
        "schemaVersion": 1,
        "messageId": f"msg-{counter:03d}",
        "fromActor": from_actor,
        "toActor": to_actor,
        "subject": subject,
        "body": body,
        "read": False,
        "timestampUtc": now,
    }

    path = _inbox_path(workspace, run_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message, ensure_ascii=False) + "\n")

    return message


def inbox_receive(workspace, run_id, actor):
    """Return unread messages for the given actor and mark them as read.

    Parameters
    ----------
    workspace : str
        Absolute path to the .teamloop workspace.
    run_id : str
        Run identifier.
    actor : str
        Recipient actor whose inbox to retrieve.

    Returns
    -------
    list[dict]
        Unread messages addressed to *actor* (now marked read).
    """
    messages = _read_inbox(workspace, run_id)

    unread = [m for m in messages if m.get("toActor") == actor and not m.get("read")]

    # Mark retrieved messages as read
    for m in messages:
        if m.get("toActor") == actor and not m.get("read"):
            m["read"] = True
    _write_inbox(workspace, run_id, messages)

    return unread


def inbox_stats(workspace, run_id):
    """Return count of unread and read messages for the run.

    Parameters
    ----------
    workspace : str
        Absolute path to the .teamloop workspace.
    run_id : str
        Run identifier.

    Returns
    -------
    dict
        {"unread": int, "read": int, "total": int}
    """
    messages = _read_inbox(workspace, run_id)
    read = sum(1 for m in messages if m.get("read"))
    unread = len(messages) - read
    return {
        "unread": unread,
        "read": read,
        "total": len(messages),
    }
