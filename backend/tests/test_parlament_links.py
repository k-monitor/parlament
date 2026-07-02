"""Unit tests for parlament.hu deep-link fragment building."""
import base64
import gzip
import json

from app.parlament_links import bill_page_url, vote_page_url


def _decode(url: str) -> dict:
    tok = url.split("cv1gzb-", 1)[1].translate(str.maketrans("_-", "/+"))
    tok += "=" * (-len(tok) % 4)
    return json.loads(gzip.decompress(base64.b64decode(tok)).decode("utf-8"))


def test_bill_page_url_matches_reference():
    # Byte-for-byte gzip output isn't stable across zlib builds, but the decoded
    # state must equal what the live site produced for this bill.
    uid = "988b700c-0acb-4573-a15d-a8fe96bae550"
    reference = ("H4sIAAAAAAAAA9WYUW-bMBDHv4ufw0bVZk3ztrZ7qLStldIvcMCFWBgfsi_bQsV3"
                 "7xGiJExt1KgCyW_22Xf3vx8G27yoCnJUc6UdlWA3-K8ix1R83fUjyIANVNFfzas"
                 "oJcsOUj49qiYq0TbTNlfzF2nWxOwh_6Wt_t7Nb-28qdq8noFRPFpntCwjTTNRWF"
                 "a8ebB8e4izm2_JlWCOHezamCOXn9rzEzgokdF92HHBTvKc7Qs1WQtGF-gy9PdAX"
                 "6RAWNDapXjk3xp9ZzyuVNjvcvlt71FCPWQy_2Y2S67jOI1iSJPoanp9GcHFNItg"
                 "tsSbbwngdBorSU8VWjVnt8au_UTe68TgHa0tS8wlGC9DHeNtvvZZx03zpvTqjco"
                 "Ptr7wM6U2k8M60L-xbN0NBcXrHf3DQss-SWg0OtmZKM7DsETjazJgIKx37D_dw6"
                 "6VlQBwOqMiKEQ91YMDkjCybUEeHqS-8mFByaZLhazZwCj1ZQ-MqDsA3e43hR8eS"
                 "5TzU2DITpYxCsKgwY2Pa1HDH6jBgw6RV1_9KMCoWFEd7qf_ZBWjAORncPkGDO2_"
                 "EmEifK-OYSHaIC87drwrzj6VyKvRaw6UVE_-kMgkaW4oEU3bnyWv5uhtCcIRAAA")
    expected = _decode("cv1gzb-" + reference)
    built = _decode(bill_page_url(uid))
    assert built == expected
    assert bill_page_url(uid).startswith(
        "https://www.parlament.hu/web/guest/iromanyok#page=cv1gzb-")


def test_bill_page_url_requires_uuid():
    assert bill_page_url(None) is None
    assert bill_page_url("T/174") is None          # a bill number, not a uuid
    assert bill_page_url("bill-uuid-1") is None     # fixture-style placeholder id


def test_vote_page_url_matches_reference():
    uid = "b4f22b2f-e6c0-4b14-b24e-63d6b0d1127e"
    reference = ("H4sIAAAAAAAAA9VUy07DMBD8F59raENUpNwoCKniUqlfsE62JYprW_am0Fb5d-y"
                 "EUFdCog8B6s1e7-zMztreMQNLZBlzW1jDFpyu8N1oS7q67UMcCiAJhr-V9Mpzrc"
                 "hCTj8cswETpSpKtWTZjuHK0GaqaLIP0cYEXqXtCqTPDkBUxDJVS9kMWIVmXTqUO"
                 "kp2BIRx7i5eFqBvvBSY69rmGMFC0HXBA6wBCysktM7vPKU2qFhGtsZuPdPOlULi"
                 "o64V-ZwFSOePOhUtPlg3bBqPDeRf9SLufSymbiEdXVu0iRueFsf409s_A6r000W"
                 "tMxMomUgXSSKSBcdxPuSpGKVcJCny8V0xFsNiNErukV1oU6_6wV-YcG90deXSj5_"
                 "5SVIjvsnm2UKVl9c35APlf2HUS_-ELrVq_lnyHyw76OEU087Q_N038ltz8lxLqY"
                 "X3oP3_PgDnEK0ZeQYAAA")
    expected = _decode("cv1gzb-" + reference)
    assert _decode(vote_page_url(uid)) == expected
    assert vote_page_url(uid).startswith(
        "https://www.parlament.hu/web/guest/szavazasok#page=cv1gzb-")


def test_vote_page_url_requires_uuid():
    assert vote_page_url(None) is None
    assert vote_page_url("2741490") is None
