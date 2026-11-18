"""Tests for real Sigstore bundle verification (``puspa.policy.sigstore_verify``).

Covers: a real staging bundle verifies offline (with sockets disabled,
so no Fulcio or Rekor call can happen); tampered artifact, forged
signature, tampered certificate, expired replacement certificate, and
tampered transparency-log inclusion proof are all rejected; wrong
identity or issuer rejected; malformed or oversized input rejected
without raising; staging and production trust roots are not
interchangeable; a missing ``sigstore`` library fails closed.

Fixture. ``_ARTIFACT_V3`` and ``_BUNDLE_V3_B64`` are the Sigstore
project's public test assets, copied without modification from
https://github.com/sigstore/sigstore-python/tree/7ee385ac8be192771bff1cc553434b11282ecce6/test/assets
(``bundle_v3.txt`` and ``bundle_v3.txt.sigstore``), Apache-2.0,
Copyright The Sigstore Authors. The same files are also vendored
under ``nanobot/tests/fixtures/sigstore/`` with their README and
LICENSE; they are embedded here so these tests do not depend on the
nanobot read-reference clone being present. The bundle is a staging
bundle: its certificate is a real short-lived Fulcio staging
certificate for ``william@yossarian.net`` via
``https://github.com/login/oauth``, logged in the staging Rekor log.

Expiry semantics. The fixture certificate was valid only
2024-04-02 19:19-19:29 UTC and is long expired now, yet the bundle
still verifies: Sigstore judges certificate validity at the Rekor
integrated time recorded in the bundle (19:19:09 UTC, inside the
window), not at verification time. That is correct Sigstore
behavior. The expired-certificate rejection test below therefore
swaps in a self-signed certificate whose validity window never
covered the signing time.
"""

import base64
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

import puspa.policy.sigstore_verify as sigstore_verify
from puspa.policy.sigstore_verify import (
    MAX_ARTIFACT_BYTES,
    MAX_BUNDLE_BYTES,
    verify_sigstore_bundle,
)

IDENTITY = "william@yossarian.net"
ISSUER = "https://github.com/login/oauth"

_ARTIFACT_V3_B64 = (
    "RE8gTk9UIE1PRElGWSBNRSEKCnRoaXMgaXMgdGhlIGlucHV0IGZvciBidW5kbGVfdjMsIHdoaWNo"
    "IHRlc3RzIHN1cHBvcnQgZm9yICJ2MyIgYnVuZGxlcy4KCkRPIE5PVCBNT0RJRlkgTUUhCg=="
)

_BUNDLE_V3_B64 = (
    "ewogICAgIm1lZGlhVHlwZSI6ICJhcHBsaWNhdGlvbi92bmQuZGV2LnNpZ3N0b3JlLmJ1bmRsZS52"
    "MC4zK2pzb24iLAogICAgInZlcmlmaWNhdGlvbk1hdGVyaWFsIjogewogICAgICAgICJjZXJ0aWZp"
    "Y2F0ZSI6IHsKICAgICAgICAgICAgInJhd0J5dGVzIjogIk1JSUMxRENDQWxxZ0F3SUJBZ0lVTzN0"
    "bFZiTHR2TFBwKzZ6R090ZXAxU1BrUmlnd0NnWUlLb1pJemowRUF3TXdOekVWTUJNR0ExVUVDaE1N"
    "YzJsbmMzUnZjbVV1WkdWMk1SNHdIQVlEVlFRREV4VnphV2R6ZEc5eVpTMXBiblJsY20xbFpHbGhk"
    "R1V3SGhjTk1qUXdOREF5TVRreE9UQTVXaGNOTWpRd05EQXlNVGt5T1RBNVdqQUFNRmt3RXdZSEtv"
    "Wkl6ajBDQVFZSUtvWkl6ajBEQVFjRFFnQUVOZHJmcGdOVTFSam16K2o2NXJwSldLYzA4cnVLWXk0"
    "Rlg3bm1tT25iYXVGWmltc1FYcmR5RFNYS05SdEVYWDRYM3QvQW10K2V1d1BEQmgrZXE3QkNucU9D"
    "QVhrd2dnRjFNQTRHQTFVZER3RUIvd1FFQXdJSGdEQVRCZ05WSFNVRUREQUtCZ2dyQmdFRkJRY0RB"
    "ekFkQmdOVkhRNEVGZ1FVR1JsQmhEMHd2ekFmTGIyZE1XT2dQcnJKdVJrd0h3WURWUjBqQkJnd0Zv"
    "QVVjWVl3cGhSOFltLzU5OWIwQlJwL1gvL3JiNnd3SXdZRFZSMFJBUUgvQkJrd0Y0RVZkMmxzYkds"
    "aGJVQjViM056WVhKcFlXNHVibVYwTUN3R0Npc0dBUVFCZzc4d0FRRUVIbWgwZEhCek9pOHZaMmww"
    "YUhWaUxtTnZiUzlzYjJkcGJpOXZZWFYwYURBdUJnb3JCZ0VFQVlPL01BRUlCQ0FNSG1oMGRIQnpP"
    "aTh2WjJsMGFIVmlMbU52YlM5c2IyZHBiaTl2WVhWMGFEQ0JpZ1lLS3dZQkJBSFdlUUlFQWdSOEJI"
    "b0FlQUIyQUNzd3ZOeG9pTW5pNGRnbUtWNTBIMGc1TVpZQzhwd3p5MTVEUVA2eXJJWjZBQUFCanFC"
    "QVFaNEFBQVFEQUVjd1JRSWdlV1VtdG5EME1GVWw1a2tYN25iTWRMV0NzREdJUHpkSWxOK1dhWkYw"
    "VG1rQ0lRQzcrMzFzYXFyRmU5Um1kdVZaMmR4WGhVUHJhamx0dVNESGIxdlNHT2N1SGpBS0JnZ3Fo"
    "a2pPUFFRREF3Tm9BREJsQWpFQW4yK3V1TEhzbkg5RGI3emtJZEY2NVloaVhiZ01NRi8vaUhjK0Iv"
    "UUVUSzBIWVZjT1BUSzNwNDZGVXpYRkQ2eHJBakFPMmhya2ZqQktBTktqSkp4SFYzRlZydFMrVFIw"
    "R0NQMEh6QzNEN0JyOTVUWHpmTzcrajREZDgvTi9hQXI2SWJzPSIKICAgICAgICB9LAogICAgICAg"
    "ICJ0bG9nRW50cmllcyI6IFsKICAgICAgICAgICAgewogICAgICAgICAgICAgICAgImxvZ0luZGV4"
    "IjogIjI1OTE1OTU2IiwKICAgICAgICAgICAgICAgICJsb2dJZCI6IHsKICAgICAgICAgICAgICAg"
    "ICAgICAia2V5SWQiOiAiMHk4d284TXRZNXdyZGlJRm9oeDdzSGVJNW9LRHBLNXZRaEdISTZHK3BK"
    "WT0iCiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAgICAgImtpbmRWZXJzaW9uIjogewog"
    "ICAgICAgICAgICAgICAgICAgICJraW5kIjogImhhc2hlZHJla29yZCIsCiAgICAgICAgICAgICAg"
    "ICAgICAgInZlcnNpb24iOiAiMC4wLjEiCiAgICAgICAgICAgICAgICB9LAogICAgICAgICAgICAg"
    "ICAgImludGVncmF0ZWRUaW1lIjogIjE3MTIwODU1NDkiLAogICAgICAgICAgICAgICAgImluY2x1"
    "c2lvblByb21pc2UiOiB7CiAgICAgICAgICAgICAgICAgICAgInNpZ25lZEVudHJ5VGltZXN0YW1w"
    "IjogIk1FWUNJUUQyS1hXMU5wcFVoa1BQekdSOE5yVUl5TitNelpTU3FHWlFPN0N6dmhTbllnSWhB"
    "TzlBSHpqYnNyMUFIWFJIbUVwZFBaY29GSEV3d01UZ2Zxd2pvT1hWTW1xTiIKICAgICAgICAgICAg"
    "ICAgIH0sCiAgICAgICAgICAgICAgICAiaW5jbHVzaW9uUHJvb2YiOiB7CiAgICAgICAgICAgICAg"
    "ICAgICAgImxvZ0luZGV4IjogIjI1OTAxMTM3IiwKICAgICAgICAgICAgICAgICAgICAicm9vdEhh"
    "c2giOiAiaUdBb0hjY0pJeUZlbUZ4bUVmdHRpMllDOGh2UHFpeEJpNXkxRXl2ZkY0Yz0iLAogICAg"
    "ICAgICAgICAgICAgICAgICJ0cmVlU2l6ZSI6ICIyNTkwMTEzOCIsCiAgICAgICAgICAgICAgICAg"
    "ICAgImhhc2hlcyI6IFsKICAgICAgICAgICAgICAgICAgICAgICAgIlVIVXIrbHZ4RU5JK0c5MDJv"
    "RXNGVzVvdlFJTGdxTzltVVdXeHZ2d0haWmM9IiwKICAgICAgICAgICAgICAgICAgICAgICAgIklj"
    "TUJzYkgzR1JXOEZYMkNpTC9sak1iNDV2em1FTm1ocDVZcC83SVc5OTg9IiwKICAgICAgICAgICAg"
    "ICAgICAgICAgICAgIlN4QzZucjB6UCthNmtXYjZuTzJmbUV0ejhCWUFicUVYYytkc3FHTGRSUE09"
    "IiwKICAgICAgICAgICAgICAgICAgICAgICAgInNwcFpSU3ovdmRlTGxhdmd2SUNyWEhMZVJlTVRK"
    "dzk4YnM5SEowSThXbkU9IiwKICAgICAgICAgICAgICAgICAgICAgICAgImM4bENTdUJTNk16clJu"
    "dDZPaXlZanFoVHl4VUkvMjJncFZCN2RibGZEaXM9IiwKICAgICAgICAgICAgICAgICAgICAgICAg"
    "ImVKazY0SjZjTXBJbGpQU1gvNzJrSDBraUllRWx5eXBRbTV2SjJnTU15SHc9IiwKICAgICAgICAg"
    "ICAgICAgICAgICAgICAgImhiSUsram1Bd1FqVTdZaTNpS3ZuZlIxdTdHTmlwcGs3UXNSd0pYSXVS"
    "YXc9IiwKICAgICAgICAgICAgICAgICAgICAgICAgInRwSFdJRUIydk5VNVptQzY4ZGoxSGg5Y3dR"
    "SzA4M296b2dBNnpKM2NKOEE9IiwKICAgICAgICAgICAgICAgICAgICAgICAgImFydnV6QWlwVUox"
    "NG5EajE0T0JsdmtNU2ljamRzRTlFdXMzaHE5SnBxZGs9IiwKICAgICAgICAgICAgICAgICAgICAg"
    "ICAgIkVkdWw0VzQxTzNFZnhLRUVNbFgyblcwK0dUZ0N2MDBuR21jcHdoQUxnVkE9IiwKICAgICAg"
    "ICAgICAgICAgICAgICAgICAgInJCV0IzNytId2tUWmdEdjByTXRHQlVvREkwVVpxY2dEWnA0OE02"
    "Q2FVbEE9IgogICAgICAgICAgICAgICAgICAgIF0sCiAgICAgICAgICAgICAgICAgICAgImNoZWNr"
    "cG9pbnQiOiB7CiAgICAgICAgICAgICAgICAgICAgICAgICJlbnZlbG9wZSI6ICJyZWtvci5zaWdz"
    "dGFnZS5kZXYgLSA4MDUwOTA5MjY0NTY1NDQ3NTI1XG4yNTkwMTEzOFxuaUdBb0hjY0pJeUZlbUZ4"
    "bUVmdHRpMllDOGh2UHFpeEJpNXkxRXl2ZkY0Yz1cblxuXHUyMDE0IHJla29yLnNpZ3N0YWdlLmRl"
    "diAweTh3b3pCRkFpQU1KSkxibk5Pbm1pek1iVkJ6OS9BL3FuTUsxNUJ1ZFdvWmt1RStvYkQ2Q0FJ"
    "aEFKZjZBM2gyaU9wdWh6L2R1RWhHM2ZiQVFHOVBYbG40d1hQSEZCVDV3VDFhXG4iCiAgICAgICAg"
    "ICAgICAgICAgICAgfQogICAgICAgICAgICAgICAgfSwKICAgICAgICAgICAgICAgICJjYW5vbmlj"
    "YWxpemVkQm9keSI6ICJleUpoY0dsV1pYSnphVzl1SWpvaU1DNHdMakVpTENKcmFXNWtJam9pYUdG"
    "emFHVmtjbVZyYjNKa0lpd2ljM0JsWXlJNmV5SmtZWFJoSWpwN0ltaGhjMmdpT25zaVlXeG5iM0pw"
    "ZEdodElqb2ljMmhoTWpVMklpd2lkbUZzZFdVaU9pSTFaVFpoWlRsa1pUVTRZekV4TnpkaVpXRTJN"
    "VFZpTkdaalltWmlNbVprTmpnNFpUaGpOR0kxTVdNeVpUVTJZalpoTXpobE9ERTNPRE16WldNeU5H"
    "RXlJbjE5TENKemFXZHVZWFIxY21VaU9uc2lZMjl1ZEdWdWRDSTZJazFGVlVOSlJGRlRTbWs1WVdW"
    "eWRGRm9iVlFyWTJVeGFrdE9aRU5sTkV0VFkzTkxSM0U1WmxCdE16UXlNa1JDVTBGcFJVRm9hakZ6"
    "ZUZvNU5tOXlTVlJ6VVhoNVRVeEpSRkpLYVcxd2Iza3hTakZOZVdac1kxRldkMnRyZW1oelBTSXNJ"
    "bkIxWW14cFkwdGxlU0k2ZXlKamIyNTBaVzUwSWpvaVRGTXdkRXhUTVVOU1ZXUktWR2xDUkZKV1Ns"
    "VlRWVnBLVVRCR1ZWSlRNSFJNVXpCMFEyc3hTbE5WVFhoU1JVNUVVVmQ0ZUZvd1JqTlRWVXBDV2pC"
    "c1ZsUjZUakJpUmxwcFZFaFNNbFJHUW5kTGVsbzJVakE1TUZwWVFYaFZNVUp5Vlcxc2JtUXdUbTVY"
    "Vld4TVlqRndTbVZ0YjNkU1ZVWXpWRmhqUzFSdWNFWldhekZEVkZWa1FrMVdWa1pSTW1oT1ZGZE5l"
    "V0pITldwTk1Vb3lXVEl4Vm1SV2NFaFdha3BPVldwU00xTkZSbHBTUmxwU1ZWVlNSbVZHV2paWlZt"
    "UnJaVzFTU0U5WWJHRlZla1ozV1cwMVUySkJjR3BpVkVaelYydGtjMkZIVWtoV1dHUkpZVWRPVDFS"
    "WGNGSmtNRFZGVVZoc1RsWkhkRFJVTVZKQ1RsWmtiMWt3TlU1aGJFWXpWR3RTUW1WVk1WVmhNMnhR"
    "VmtWRk1WWXljRUpSVlRGSFlUTmtSbVF4YkVsRGEzUjJWMnRzTm1GcVFrUlJWa1phVTFWMGRsZHJi"
    "RFpoYWtKRlVWWkdhbEpHUm01UlZWWlBXa2hLYldOSFpFOVdWRVpUWVcweE5rc3liekpPV0VwM1Uy"
    "eGtURmw2UVRSamJsWk1WMWhyTUZKc1p6TmliVEJMWWxVNWRWbHRSakZTYkhCd1lsaE9VbGRJU210"
    "bFZWSlVWMFYwVDFWdVVrWlhSbWN3VjBST01Fd3dSblJrUTNSc1pGaGtVVkpGU205TE1sWjRUakJL"
    "UkdKdVJsQlJNRVpaWVROa2Jsb3dXWGhVVlVVd1VuZHdRazFXVm10U1NHUkdVV2s1TTFWVlZrSmtN"
    "R3hKV2pCU1FsWkZTbTVVYkZwSlZURldSbEpGVWtKVE1FcHVXak5LUTFvd1ZrZFJiRVpxVWtWR05s"
    "RlhVa05hTURWWFUwWkZNRkpWV201VlZsWklWVzE0UTBOdGFFVk5TR1F5Wld0R2JWUkhTWGxhUlRG"
    "WVZESmtVV051U2t0a1ZrcHlaREJvTTFkVlVsZFZha0p4VVd0S2JtUXdXblpSVmxacVYxWnNNMk5I"
    "YUZOUFJteDBUSHBWTlU5WFNYZFJiRXAzVERGbmRrd3pTVXRaYWxvelpEQnNNMWRWVWxkVmFrSlRV"
    "VlpHU1V3d1NrTmhNMlJIVGtWV1YxcEVTbk5qTWtwSVlrZG9hVlpWU1RGWmFrNVBaV3hzV1ZOdVFs"
    "cFdlbEl4V1cweFYwMUZNVVJrTUdSRVlWaE9TRkZXUmxKUlozQnVUbnBvTTFGV1JrWlNWV2gwWVVS"
    "Q2ExTkZTalpVTW1zMFpHeHZlV0pFUW1oVFJscHdWRWN4VDJSdFNsUlBXRTVwVFcxU2QxbHRhelZr"
    "Ykd4WlZtcENhRkpGUmpGUmJXUjJZMnRLYmxKVlZrSlhWVGgyUTJzeFFsSlZiRU5STUVaT1UwY3hi"
    "MDFIVWtsUmJuQlFZVlJvTWxkcVNuTk5SMFpKVm0xc1RXSlZOVEpaYkUwMVl6SkplVnBJUW1saFZH"
    "d3lWMVpvVjAxSFJrVlJNRXB3V2pGc1RGTXpaRnBSYTBwQ1UwWmpTMXBXUmtwU1ZVWnVWV3BvUTFO"
    "SE9VSmFWVVpEVFd0R1JHTXpaREpVYm1oMllWVXhkV0ZVVW10YU1qRk1WbXBWZDFORVFtNU9WVEZo"
    "VjFWTk5HTklaRFpsVkVVeFVrWkdVVTV1YkhsVFZtOHlVVlZHUWxGbmNIRmpWVXBDVlZadk1GRlZS"
    "a0pWVlZKQ1VsZE9NMVZzUmtwYU1sWllWbGN4TUdKclVYZFVWVnBXWWtSV2NtRXhaek5pYlVwT1dr"
    "VjRXRkV6VGtWU01HeFJaVzFTU21KRk5ISldNa1poVW1wQ1ZXSlhkRVJEYTJ4U1VYcGpjazE2Um5w"
    "WldFWjVVbTFWTlZWdE1XdGtWbHBoVFcxU05GZEhhRlpWU0Vwb1lXMTRNR1JXVGtWVFIwbDRaR3hP"
    "U0ZReVRqRlRSM0JDVXpCS2Jsb3pSbTloTW5CUVZVWkdVbEpGUmpOVWJUaExVVlZTUTJKRlJuRlNW"
    "VVoxVFdsME1XUlZlRWxqTWpWSlQxVlNhVTR6Y0hKVFYxSkhUbXBXV21GSGJGbFpiV1JPVkZWWmRr"
    "d3liRWxaZVhSRFRERkdSbFpGYzNkVFJteFhXVEE1VVZaRmMzcGpSRkV5VW1kd1ZtVnNhRWRTUkZv"
    "MFkydEdjVkZWT0hsaFNFcHlXbTF3UTFNd1JrOVRNbkJMVTI1b1NWWnFUa2RXYmtvd1ZYbDBWVlZx"
    "UWtoUk1VRjNVMGh3UkUwd1VUTlJia2sxVGxaU1dXVnRXbEJPZVhSeFRrVlNhME5xWjNaVWFUbG9V"
    "VmhKTWxOWFNucFFVVzkwVEZNd2RFeFZWazlTUTBKRVVsWktWVk5WV2twUk1FWlZVbE13ZEV4VE1I"
    "UkRaejA5SW4xOWZYMD0iCiAgICAgICAgICAgIH0KICAgICAgICBdCiAgICB9LAogICAgIm1lc3Nh"
    "Z2VTaWduYXR1cmUiOiB7CiAgICAgICAgIm1lc3NhZ2VEaWdlc3QiOiB7CiAgICAgICAgICAgICJh"
    "bGdvcml0aG0iOiAiU0hBMl8yNTYiLAogICAgICAgICAgICAiZGlnZXN0IjogIlhtcnAzbGpCRjN2"
    "cVlWdFB5L3N2MW9qb3hMVWNMbGEybzQ2QmVEUHNKS0k9IgogICAgICAgIH0sCiAgICAgICAgInNp"
    "Z25hdHVyZSI6ICJNRVVDSURRU0ppOWFlcnRRaG1UK2NlMWpLTmRDZTRLU2NzS0dxOWZQbTM0MjJE"
    "QlNBaUVBaGoxc3haOTZvcklUc1F4eU1MSURSSmltcG95MUoxTXlmbGNRVndra3pocz0iCiAgICB9"
    "Cn0K"
)

def _artifact() -> bytes:
    return base64.b64decode(_ARTIFACT_V3_B64)


def _bundle_bytes() -> bytes:
    return base64.b64decode(_BUNDLE_V3_B64)


def _bundle_dict() -> dict:
    return json.loads(_bundle_bytes())


def _mutated_bundle(**changes) -> bytes:
    """Bundle bytes with top-level JSON paths replaced.

    ``changes`` maps a dotted path (for example
    ``"messageSignature.signature"``) to its new value.
    """
    data = _bundle_dict()
    for path, value in changes.items():
        node = data
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return json.dumps(data).encode("utf-8")


def _flip_first_char(value: str) -> str:
    return ("A" if value[0] != "A" else "B") + value[1:]


def _expired_certificate_bundle() -> bytes:
    """The fixture bundle with its certificate replaced by an
    expired self-signed certificate carrying the same identity.

    The replacement was never issued by Fulcio and its validity
    window (a year ago) never covered the Rekor integrated time,
    so verification must reject it.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired fixture")])
    expired = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=365))
        .not_valid_after(datetime.now(timezone.utc) - timedelta(days=364))
        .add_extension(
            x509.SubjectAlternativeName([x509.RFC822Name(IDENTITY)]), False
        )
        .sign(key, hashes.SHA256())
    )
    raw = base64.b64encode(expired.public_bytes(Encoding.DER)).decode("ascii")
    return _mutated_bundle(**{"verificationMaterial.certificate.rawBytes": raw})


def test_valid_staging_bundle_verifies():
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), IDENTITY, ISSUER, trust_root="staging"
    )
    assert (ok, reason) == (True, "ok")
    # The fixture certificate expired in 2024, yet the bundle
    # verifies: validity is judged at the Rekor integrated time.
    cert = x509.load_der_x509_certificate(
        base64.b64decode(
            _bundle_dict()["verificationMaterial"]["certificate"]["rawBytes"]
        )
    )
    assert cert.not_valid_after_utc < datetime.now(timezone.utc)


def test_verification_uses_no_network(monkeypatch):
    def _no_socket(*args, **kwargs):
        raise OSError("network disabled in test")

    monkeypatch.setattr(socket, "socket", _no_socket)
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), IDENTITY, ISSUER, trust_root="staging"
    )
    assert (ok, reason) == (True, "ok")


def test_tampered_artifact_rejected():
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact() + b"modified", IDENTITY, ISSUER,
        trust_root="staging",
    )
    assert ok is False
    assert reason


def test_forged_signature_rejected():
    original = _bundle_dict()["messageSignature"]["signature"]
    forged = _mutated_bundle(
        **{"messageSignature.signature": _flip_first_char(original)}
    )
    ok, reason = verify_sigstore_bundle(
        forged, _artifact(), IDENTITY, ISSUER, trust_root="staging"
    )
    assert ok is False
    assert reason


def test_tampered_certificate_rejected():
    original = _bundle_dict()["verificationMaterial"]["certificate"]["rawBytes"]
    tampered = _mutated_bundle(
        **{"verificationMaterial.certificate.rawBytes": _flip_first_char(original)}
    )
    ok, reason = verify_sigstore_bundle(
        tampered, _artifact(), IDENTITY, ISSUER, trust_root="staging"
    )
    assert ok is False
    assert reason


def test_expired_certificate_rejected():
    ok, reason = verify_sigstore_bundle(
        _expired_certificate_bundle(), _artifact(), IDENTITY, ISSUER,
        trust_root="staging",
    )
    assert ok is False
    assert reason


def test_tampered_inclusion_proof_rejected():
    proof = _bundle_dict()["verificationMaterial"]["tlogEntries"][0]["inclusionProof"]
    original = proof["hashes"][0]
    tampered = _mutated_bundle(
        **{
            "verificationMaterial.tlogEntries": [
                {
                    **_bundle_dict()["verificationMaterial"]["tlogEntries"][0],
                    "inclusionProof": {
                        **proof,
                        "hashes": [_flip_first_char(original)] + proof["hashes"][1:],
                    },
                }
            ]
        }
    )
    ok, reason = verify_sigstore_bundle(
        tampered, _artifact(), IDENTITY, ISSUER, trust_root="staging"
    )
    assert ok is False
    assert reason


def test_wrong_identity_rejected():
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), "attacker@example.com", ISSUER,
        trust_root="staging",
    )
    assert ok is False
    assert reason


def test_wrong_issuer_rejected():
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), IDENTITY, "https://example.com",
        trust_root="staging",
    )
    assert ok is False
    assert reason


@pytest.mark.parametrize("bad_bundle", [b"not a bundle", b"", b"{}", b"[]", b"null"])
def test_malformed_bundle_rejected(bad_bundle):
    ok, reason = verify_sigstore_bundle(
        bad_bundle, _artifact(), IDENTITY, ISSUER, trust_root="staging"
    )
    assert ok is False
    assert reason


@pytest.mark.parametrize("bad_bundle", [None, "not bytes", 123])
def test_non_bytes_bundle_rejected(bad_bundle):
    ok, reason = verify_sigstore_bundle(
        bad_bundle, _artifact(), IDENTITY, ISSUER, trust_root="staging"
    )
    assert ok is False
    assert reason


def test_oversized_inputs_rejected():
    ok, _ = verify_sigstore_bundle(
        b"x" * (MAX_BUNDLE_BYTES + 1), _artifact(), IDENTITY, ISSUER,
        trust_root="staging",
    )
    assert ok is False
    ok, _ = verify_sigstore_bundle(
        _bundle_bytes(), b"x" * (MAX_ARTIFACT_BYTES + 1), IDENTITY, ISSUER,
        trust_root="staging",
    )
    assert ok is False


@pytest.mark.parametrize(
    "identity,issuer", [("", ISSUER), (IDENTITY, ""), (None, ISSUER), (IDENTITY, None)]
)
def test_missing_identity_or_issuer_rejected(identity, issuer):
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), identity, issuer, trust_root="staging"
    )
    assert ok is False
    assert reason


def test_staging_bundle_rejected_under_production_root():
    # The two trust roots are not interchangeable: a staging bundle
    # must not verify against the production root, and vice versa.
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), IDENTITY, ISSUER, trust_root="production"
    )
    assert ok is False
    assert reason


def test_trust_root_from_file_path():
    from importlib.resources import as_file, files

    resource = files("sigstore._store").joinpath(
        "https%3A%2F%2Ftuf-repo-cdn.sigstage.dev/trusted_root.json"
    )
    with as_file(resource) as path:
        ok, reason = verify_sigstore_bundle(
            _bundle_bytes(), _artifact(), IDENTITY, ISSUER, trust_root=str(path)
        )
    assert (ok, reason) == (True, "ok")


def test_unknown_trust_root_rejected():
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), IDENTITY, ISSUER, trust_root="no-such-root"
    )
    assert ok is False
    assert reason


def test_missing_library_fails_closed(monkeypatch):
    def _no_library():
        raise ImportError("sigstore is not installed")

    monkeypatch.setattr(sigstore_verify, "_load_sigstore", _no_library)
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), IDENTITY, ISSUER, trust_root="staging"
    )
    assert ok is False
    assert "not installed" in reason
