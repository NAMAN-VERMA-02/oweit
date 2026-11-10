import pytest

from oweit.prefilter import is_candidate


@pytest.mark.parametrize(
    "text",
    [
        "I'll send it by Sunday",
        "ill share the ppt tonight",
        "Will send by tonight",
        "I'm gonna call her tomorrow",
        "let me check and get back to you",
        "on it!",
        "kal bhej dunga",
        "haan kar dungi pakka",
        "hum log aa jayenge 5 baje",  # -enge
        "abhi bhejta hu",
        "I can drop it off by Friday",
        "I promise I'll pay you back",
        "yep, done 👍",
        "sure, I can take it",
        "Yes ma'am, 4 works.",
        "Confirmed, see you on Saturday.",
    ],
)
def test_commitment_like_passes(text):
    assert is_candidate(text)


@pytest.mark.parametrize(
    "text",
    [
        "sent it",
        "bhej diya",
        "haha",
        "can you send the notes?",
        "ok",
        "where are you",
        "thanks bro",
        "done with the exam finally",
    ],
)
def test_plain_messages_dropped(text):
    assert not is_candidate(text)
