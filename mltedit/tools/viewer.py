"""The live viewer tool."""
from .. import server as sv
from . import tool


@tool
def open_viewer() -> dict:
    """Start (once) the live viewer and return its URL: a local web page (127.0.0.1, private token in the address) that plays the current edit with its
    sound and follows it while you edit - when the project changes the player reloads at the same playback time; only the 2-second segments an edit
    touches are rendered again. It shows the timeline and the edit list with their ids. Open it in a browser (it is not an image: use get_still or
    get_contact_sheet to LOOK at the edit yourself). The segments are made from the previews' proxies and are not the export."""
    from .. import viewer
    v = viewer.start(sv.HOME)
    return {"url": v.url, "note": "open it in a browser on this machine; it updates by itself; segments are rendered when first played"}
