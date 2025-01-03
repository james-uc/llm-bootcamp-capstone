import sqlite3
from config import CONFIG


def get_summaries():
    conn = sqlite3.connect(CONFIG["database_path"])
    cursor = conn.cursor()
    cursor.execute("SELECT id, summary FROM papers")
    summaries = cursor.fetchall()
    conn.close()
    return summaries


def get_full_text(id):
    conn = sqlite3.connect(CONFIG["database_path"])
    cursor = conn.cursor()
    cursor.execute("SELECT full_text FROM papers WHERE id = ?", (id,))
    full_text = cursor.fetchone()[0]
    conn.close()
    return full_text


def extract_tag_content(text: str, tag_name: str) -> str | None:
    """
    Extract content between XML-style tags.

    Args:
        text: The text containing the tags
        tag_name: Name of the tag to find

    Returns:
        String content between tags if found, None if not found

    Example:
        >>> text = "before <foo>content</foo> after"
        >>> extract_tag_content(text, "foo")
        'content'
    """
    import re

    pattern = f"<{tag_name}>(.*?)</{tag_name}>"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1) if match else None
