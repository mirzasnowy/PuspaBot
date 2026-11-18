"""Tests for real Sigstore bundle verification (``puspa.policy.sigstore_verify``).

Covers: a real staging bundle verifies offline (with sockets disabled,
so no Fulcio or Rekor call can happen); tampered artifact, forged
signature, tampered certificate, expired replacement certificate, and
tampered transparency-log inclusion proof are all rejected; wrong
identity or issuer rejected; malformed or oversized input rejected
without raising; staging and production trust roots are not
interchangeable; a missing ``sigstore`` library fails closed. A real
staging DSSE bundle is covered the same way: it parses as a DSSE
envelope and verifies offline at the library level
(``test_dsse_bundle_verifies_offline_with_library_verifier``), and
at the Puspa level (``test_dsse_bundle_verifies``) with the
sigstore-conformance ``a.txt`` artifact, whose sha256 matches the
in-toto statement's subject digest, so the pass also proves
artifact-to-subject binding. Tampered envelope signature, payload,
certificate, and inclusion proof, wrong identity or issuer, and the
production trust root are all rejected, each for its own tampering
rather than for a binding mismatch. The sigstore-python companion
artifact (``a.dsse.staging-rekor-v2.txt``) is rejected by the binding
check instead: its sha256 does not match the statement's subject
digest, which is why the pass test uses the conformance artifact.

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
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from puspa.policy import sigstore_verify
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

    DSSE fixture. ``_ARTIFACT_DSSE_B64`` and ``_BUNDLE_DSSE_B64`` are the
    Sigstore project's public test assets, copied without modification
    from https://github.com/sigstore/sigstore-python/tree/9cfab5d5e2467e728afbfbae1b0afc6b90e771e4/test/assets
    (``a.dsse.staging-rekor-v2.txt`` and
    ``a.dsse.staging-rekor-v2.txt.sigstore.json``), Apache-2.0,
    Copyright The Sigstore Authors. The bundle is a staging DSSE bundle:
    an in-toto statement for ``a.txt`` signed by a short-lived Fulcio
    staging certificate for the GitHub Actions workflow identity
    ``https://github.com/sigstore-conformance/extremely-dangerous-public-oidc-beacon/.github/workflows/extremely-dangerous-oidc-beacon.yml@refs/heads/main``
    via ``https://token.actions.githubusercontent.com``, logged in the
    staging Rekor log as a hashedrekord 0.0.2 entry. Known limit: the
    statement's subject digest (a0cfc712...) does not match the companion
    artifact file (sha256 e248a5db...): the upstream fixture was
    regenerated and the statement still carries the old subject digest,
    so the companion artifact is rejected by the binding check and the
    pass test uses the sigstore-conformance ``a.txt`` instead (see
    ``_ARTIFACT_DSSE_BOUND_B64``), whose sha256 is a0cfc712....
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired fixture")])
    expired = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime.now(UTC) - timedelta(days=365))
        .not_valid_after(datetime.now(UTC) - timedelta(days=364))
        .add_extension(x509.SubjectAlternativeName([x509.RFC822Name(IDENTITY)]), False)
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
        base64.b64decode(_bundle_dict()["verificationMaterial"]["certificate"]["rawBytes"])
    )
    assert cert.not_valid_after_utc < datetime.now(UTC)


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
        _bundle_bytes(),
        _artifact() + b"modified",
        IDENTITY,
        ISSUER,
        trust_root="staging",
    )
    assert ok is False
    assert reason


def test_forged_signature_rejected():
    original = _bundle_dict()["messageSignature"]["signature"]
    forged = _mutated_bundle(**{"messageSignature.signature": _flip_first_char(original)})
    ok, reason = verify_sigstore_bundle(forged, _artifact(), IDENTITY, ISSUER, trust_root="staging")
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
        _expired_certificate_bundle(),
        _artifact(),
        IDENTITY,
        ISSUER,
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
                        "hashes": [_flip_first_char(original), *proof["hashes"][1:]],
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
        _bundle_bytes(),
        _artifact(),
        "attacker@example.com",
        ISSUER,
        trust_root="staging",
    )
    assert ok is False
    assert reason


def test_wrong_issuer_rejected():
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(),
        _artifact(),
        IDENTITY,
        "https://example.com",
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
        b"x" * (MAX_BUNDLE_BYTES + 1),
        _artifact(),
        IDENTITY,
        ISSUER,
        trust_root="staging",
    )
    assert ok is False
    ok, _ = verify_sigstore_bundle(
        _bundle_bytes(),
        b"x" * (MAX_ARTIFACT_BYTES + 1),
        IDENTITY,
        ISSUER,
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


# --- DSSE bundle fixture -----------------------------------------
# Provenance and the known subject-digest limit are documented in
# the module docstring; the byte-identity test below pins the
# embedded blobs to the source files' sha256.

IDENTITY_DSSE = (
    "https://github.com/sigstore-conformance/extremely-dangerous-public-oidc-beacon/"
    ".github/workflows/extremely-dangerous-oidc-beacon.yml@refs/heads/main"
)
ISSUER_DSSE = "https://token.actions.githubusercontent.com"

#: sha256 of the source files, for the byte-identity check below.
_ARTIFACT_DSSE_SHA256 = "e248a5db4933dba6578200238c91a57f5e65b925b73050ae786933468b7ac101"
_BUNDLE_DSSE_SHA256 = "56e79ba9f94a34aba285769574c456601f38beeaa48a3acb67f8d5db706f9420"

#: sha256 recorded inside the DSSE payload's in-toto statement for
#: subject "a.txt". It does NOT match _ARTIFACT_DSSE_SHA256: the
#: upstream fixture was regenerated and the statement still carries
#: the old subject digest (checked against the artifact bytes, the
#: bytes without trailing newline, CRLF bytes, and the payload
#: itself; none match). It DOES match the sigstore-conformance
#: ``a.txt`` below (sha256 _ARTIFACT_DSSE_BOUND_SHA256): the
#: conformance suite's ``a.txt`` is a different file from
#: sigstore-python's ``a.dsse.staging-rekor-v2.txt`` despite the
#: same name, and the bundle was signed over the conformance one.
#: The same bundle bytes also ship as
#: ``test/assets/bundle-verify/rekor2-dsse-happy-path/bundle.sigstore.json``
#: in sigstore-conformance, whose README confirms it is the staging
#: DSSE happy path for this ``a.txt``.
_SUBJECT_DIGEST_DSSE = "a0cfc71271d6e278e57cd332ff957c3f7043fdda354c4cbb190a30d56efa01bf"

#: The bound artifact: ``a.txt`` from sigstore-conformance, copied
#: without modification from
#: https://github.com/sigstore/sigstore-conformance/tree/main/test/assets
#: (``a.txt``; the same bytes live at
#: ``test/assets/bundle-verify/a.txt``), Apache-2.0 (see the repo
#: README "Licensing" section), Copyright The Sigstore Authors. Its
#: sha256 equals the in-toto statement's subject digest, so it is
#: the artifact the DSSE pass test verifies.
_ARTIFACT_DSSE_BOUND_SHA256 = "a0cfc71271d6e278e57cd332ff957c3f7043fdda354c4cbb190a30d56efa01bf"

_ARTIFACT_DSSE_BOUND_B64 = (
    "RE8gTk9UIE1PRElGWSBNRSEKCnRoaXMgaXMgImEudHh0IiwgYSBzYW1wbGUgaW5wdXQgZm9yIHNp"
    "Z3N0b3JlLWNvbmZvcm1hbmNlJ3MgdGVzdCBzdWl0ZS4KCkRPIE5PVCBNT0RJRlkgTUUhCg=="
)

_ARTIFACT_DSSE_B64 = (
    "RE8gTk9UIE1PRElGWSBNRSEKCnRoaXMgaXMgImEudHh0IiwgYSBzYW1wbGUgaW5wdXQgZm9yIHNp"
    "Z3N0b3JlLXB5dGhvbidzIHVuaXQgdGVzdHMuCgpETyBOT1QgTU9ESUZZIE1FIQo="
)

_BUNDLE_DSSE_B64 = (
    "ewogICJtZWRpYVR5cGUiOiAiYXBwbGljYXRpb24vdm5kLmRldi5zaWdzdG9yZS5idW5kbGUudjAu"
    "Mytqc29uIiwKICAidmVyaWZpY2F0aW9uTWF0ZXJpYWwiOiB7CiAgICAiY2VydGlmaWNhdGUiOiB7"
    "CiAgICAgICJyYXdCeXRlcyI6ICJNSUlJUGpDQ0I4T2dBd0lCQWdJVWVNM05sU3ppR3F5TDllWkk2"
    "MG9aeExiOXphZ3dDZ1lJS29aSXpqMEVBd013TnpFVk1CTUdBMVVFQ2hNTWMybG5jM1J2Y21VdVpH"
    "VjJNUjR3SEFZRFZRUURFeFZ6YVdkemRHOXlaUzFwYm5SbGNtMWxaR2xoZEdVd0hoY05Nall3TlRF"
    "ek1Ua3lNek15V2hjTk1qWXdOVEV6TVRrek16TXlXakFBTUZrd0V3WUhLb1pJemowQ0FRWUlLb1pJ"
    "emowREFRY0RRZ0FFMVF5R3VYdlc2cXcvQThaR2hQTWtUZ050dUk1S0Q3OE1lbHJqMk1pUmVzNFEv"
    "MzVZSSt0QzE0WEtZalFEUURnSU9WT0RZWHQ5NDhzSnc5ekc3Z1hNa0tPQ0J1SXdnZ2JlTUE0R0Ex"
    "VWREd0VCL3dRRUF3SUhnREFUQmdOVkhTVUVEREFLQmdnckJnRUZCUWNEQXpBZEJnTlZIUTRFRmdR"
    "VU16MW9yZml6ajJibWVUU1NJS3VLOXpoNWlQY3dId1lEVlIwakJCZ3dGb0FVY1lZd3BoUjhZbS81"
    "OTliMEJScC9YLy9yYjZ3d2dhVUdBMVVkRVFFQi93U0JtakNCbDRhQmxHaDBkSEJ6T2k4dloybDBh"
    "SFZpTG1OdmJTOXphV2R6ZEc5eVpTMWpiMjVtYjNKdFlXNWpaUzlsZUhSeVpXMWxiSGt0WkdGdVoy"
    "VnliM1Z6TFhCMVlteHBZeTF2YVdSakxXSmxZV052Ymk4dVoybDBhSFZpTDNkdmNtdG1iRzkzY3k5"
    "bGVIUnlaVzFsYkhrdFpHRnVaMlZ5YjNWekxXOXBaR010WW1WaFkyOXVMbmx0YkVCeVpXWnpMMmhs"
    "WVdSekwyMWhhVzR3T1FZS0t3WUJCQUdEdnpBQkFRUXJhSFIwY0hNNkx5OTBiMnRsYmk1aFkzUnBi"
    "MjV6TG1kcGRHaDFZblZ6WlhKamIyNTBaVzUwTG1OdmJUQWZCZ29yQmdFRUFZTy9NQUVDQkJGM2Iz"
    "SnJabXh2ZDE5a2FYTndZWFJqYURBMkJnb3JCZ0VFQVlPL01BRURCQ2c1TnpkbE9HUXdPRFUxTkdO"
    "aE9XSmtZVEkzT1dNNU5tVTVNV0psTWpFeE9XWmlOamRqTnpNd01DMEdDaXNHQVFRQmc3OHdBUVFF"
    "SDBWNGRISmxiV1ZzZVNCa1lXNW5aWEp2ZFhNZ1QwbEVReUJpWldGamIyNHdTUVlLS3dZQkJBR0R2"
    "ekFCQlFRN2MybG5jM1J2Y21VdFkyOXVabTl5YldGdVkyVXZaWGgwY21WdFpXeDVMV1JoYm1kbGNt"
    "OTFjeTF3ZFdKc2FXTXRiMmxrWXkxaVpXRmpiMjR3SFFZS0t3WUJCQUdEdnpBQkJnUVBjbVZtY3k5"
    "b1pXRmtjeTl0WVdsdU1Ec0dDaXNHQVFRQmc3OHdBUWdFTFF3cmFIUjBjSE02THk5MGIydGxiaTVo"
    "WTNScGIyNXpMbWRwZEdoMVluVnpaWEpqYjI1MFpXNTBMbU52YlRDQnBnWUtLd1lCQkFHRHZ6QUJD"
    "UVNCbHd5QmxHaDBkSEJ6T2k4dloybDBhSFZpTG1OdmJTOXphV2R6ZEc5eVpTMWpiMjVtYjNKdFlX"
    "NWpaUzlsZUhSeVpXMWxiSGt0WkdGdVoyVnliM1Z6TFhCMVlteHBZeTF2YVdSakxXSmxZV052Ymk4"
    "dVoybDBhSFZpTDNkdmNtdG1iRzkzY3k5bGVIUnlaVzFsYkhrdFpHRnVaMlZ5YjNWekxXOXBaR010"
    "WW1WaFkyOXVMbmx0YkVCeVpXWnpMMmhsWVdSekwyMWhhVzR3T0FZS0t3WUJCQUdEdnpBQkNnUXFE"
    "Q2c1TnpkbE9HUXdPRFUxTkdOaE9XSmtZVEkzT1dNNU5tVTVNV0psTWpFeE9XWmlOamRqTnpNd01C"
    "MEdDaXNHQVFRQmc3OHdBUXNFRHd3TloybDBhSFZpTFdodmMzUmxaREJlQmdvckJnRUVBWU8vTUFF"
    "TUJGQU1UbWgwZEhCek9pOHZaMmwwYUhWaUxtTnZiUzl6YVdkemRHOXlaUzFqYjI1bWIzSnRZVzVq"
    "WlM5bGVIUnlaVzFsYkhrdFpHRnVaMlZ5YjNWekxYQjFZbXhwWXkxdmFXUmpMV0psWVdOdmJqQTRC"
    "Z29yQmdFRUFZTy9NQUVOQkNvTUtEazNOMlU0WkRBNE5UVTBZMkU1WW1SaE1qYzVZemsyWlRreFlt"
    "VXlNVEU1Wm1JMk4yTTNNekF3SHdZS0t3WUJCQUdEdnpBQkRnUVJEQTl5WldaekwyaGxZV1J6TDIx"
    "aGFXNHdHUVlLS3dZQkJBR0R2ekFCRHdRTERBazJNekkxT1RZNE9UY3dOd1lLS3dZQkJBR0R2ekFC"
    "RUFRcERDZG9kSFJ3Y3pvdkwyZHBkR2gxWWk1amIyMHZjMmxuYzNSdmNtVXRZMjl1Wm05eWJXRnVZ"
    "MlV3R1FZS0t3WUJCQUdEdnpBQkVRUUxEQWt4TXpFNE1EUTFOak13Z2FZR0Npc0dBUVFCZzc4d0FS"
    "SUVnWmNNZ1pSb2RIUndjem92TDJkcGRHaDFZaTVqYjIwdmMybG5jM1J2Y21VdFkyOXVabTl5YldG"
    "dVkyVXZaWGgwY21WdFpXeDVMV1JoYm1kbGNtOTFjeTF3ZFdKc2FXTXRiMmxrWXkxaVpXRmpiMjR2"
    "TG1kcGRHaDFZaTkzYjNKclpteHZkM012WlhoMGNtVnRaV3g1TFdSaGJtZGxjbTkxY3kxdmFXUmpM"
    "V0psWVdOdmJpNTViV3hBY21WbWN5OW9aV0ZrY3k5dFlXbHVNRGdHQ2lzR0FRUUJnNzh3QVJNRUtn"
    "d29PVGMzWlRoa01EZzFOVFJqWVRsaVpHRXlOemxqT1RabE9URmlaVEl4TVRsbVlqWTNZemN6TURB"
    "aEJnb3JCZ0VFQVlPL01BRVVCQk1NRVhkdmNtdG1iRzkzWDJScGMzQmhkR05vTUlHQ0Jnb3JCZ0VF"
    "QVlPL01BRVZCSFFNY21oMGRIQnpPaTh2WjJsMGFIVmlMbU52YlM5emFXZHpkRzl5WlMxamIyNW1i"
    "M0p0WVc1alpTOWxlSFJ5WlcxbGJIa3RaR0Z1WjJWeWIzVnpMWEIxWW14cFl5MXZhV1JqTFdKbFlX"
    "TnZiaTloWTNScGIyNXpMM0oxYm5Ndk1qVTRNakV5T0RJd05Ua3ZZWFIwWlcxd2RITXZNVEFXQmdv"
    "ckJnRUVBWU8vTUFFV0JBZ01CbkIxWW14cFl6Q0JsUVlLS3dZQkJBSFdlUUlFQWdTQmhnU0Jnd0NC"
    "QUg4QVBtQnhVM1J1R0pMZ2tMSTJzVWwySnk5bnRFMXZrczV2ZHhGS3R5S2dwcllBQUFHZUlzdFpr"
    "QUFJQUFBRkFBQU4zaFFFQXdCSU1FWUNJUUMyMXpmUy9rT0xUUFA4dXZTckFKMjg2WDRkdzViZUYz"
    "cGdDdEhwM0ZzdmJBSWhBTVI3d3dUV2Y5KytkdExQcE1sUm12ek9DMmd3Z1M0ajlmN2VSTHcydHhY"
    "TU1Bb0dDQ3FHU000OUJBTURBMmtBTUdZQ01RRG9UVm1PVEsyM3Q1ZUUvMUJNcjdHUHhpeXZGMytE"
    "OFhKL09reDdUUkh1aXpKamxORjVkTlJXTFhSa3A5b2tGcmNDTVFEUXBweXQ4RjF0YkZjOG1RcWly"
    "d0E5MGV5RUpmQlZqRzREMGxvTjZ0N0p3S3JPK0svUXVvRThCMW1velRaaXhJYz0iCiAgICB9LAog"
    "ICAgInRsb2dFbnRyaWVzIjogWwogICAgICB7CiAgICAgICAgImxvZ0luZGV4IjogIjQwMjY0Nzgi"
    "LAogICAgICAgICJsb2dJZCI6IHsKICAgICAgICAgICJrZXlJZCI6ICIwOU9uREtFdzcvaHBaaVlW"
    "UG9UUnpSYmdsSGswc3lsc1VvdmVnblJVbEpZPSIKICAgICAgICB9LAogICAgICAgICJraW5kVmVy"
    "c2lvbiI6IHsKICAgICAgICAgICJraW5kIjogImhhc2hlZHJla29yZCIsCiAgICAgICAgICAidmVy"
    "c2lvbiI6ICIwLjAuMiIKICAgICAgICB9LAogICAgICAgICJpbmNsdXNpb25Qcm9vZiI6IHsKICAg"
    "ICAgICAgICJsb2dJbmRleCI6ICI0MDI2NDc4IiwKICAgICAgICAgICJyb290SGFzaCI6ICI1aWFY"
    "NHhLNlBNbnA0WkZUUDRpeXdNY3dMZ2FUanpnUlRrclRPVUZJcmZZPSIsCiAgICAgICAgICAidHJl"
    "ZVNpemUiOiAiNDAyNjQ3OSIsCiAgICAgICAgICAiaGFzaGVzIjogWwogICAgICAgICAgICAieTll"
    "SC9DbC9nbEV1TE1LdHdWMGJnWithMVAvQWpvUHl2dS9pVWVhbmFJTT0iLAogICAgICAgICAgICAi"
    "bnJOUTJ2YjVKTTJObzZ6RUxadDA1b25UWXpxUU93RFVjczI0akFhNmx6az0iLAogICAgICAgICAg"
    "ICAiSGZaTzJ1Wlpib2t3T1JVRC8wZE1ScEJFb3VvMzNBYjJuUTJONnI0djY4OD0iLAogICAgICAg"
    "ICAgICAiZ3JNZFluOHRFNzNKdkpVOEl4azRobDgvZkt1MDZoVWRncFBOc3VBZmI3bz0iLAogICAg"
    "ICAgICAgICAiaTFnUTYxeGUzZjlNUDA5eTJlOVo0bjVabkRpRnVxUW1USy91N0dqaTRXcz0iLAog"
    "ICAgICAgICAgICAiVGtQQTRNdjRwbE0zYzZvZWpmNXIrUHFWeGZNUGsweCtRQWp1bXphc3dzVT0i"
    "LAogICAgICAgICAgICAid0dxNDZSZmtBOXRDWVoyQ0NPT01HUXR0eWhISXFNa3NCQWwrc29EQmdS"
    "dz0iLAogICAgICAgICAgICAiV1BXZnRSKzhRanYzcWlJQnA0OG4zUGl3ZWVqY3B1SGpva2hhU0lK"
    "YVVPbz0iLAogICAgICAgICAgICAia2dHY2NOWmpDRE5LaVFCVVNQb3JaY1RYb3k5VDBPSXlnVU5Y"
    "MlY5dEY3cz0iLAogICAgICAgICAgICAiN3ZNZWFwWXFwakRaYzhpdHQzdUY2ZEd4TDVxa2JGc1Ex"
    "OGpCaWJsSC81Zz0iLAogICAgICAgICAgICAibWE4blZqaHBKMEptZ1N6YXhoeFA3YnZIWHRpWkM2"
    "bkpYSUJtRFY4a05BST0iLAogICAgICAgICAgICAiQ1pKV0p0WVpGWVd3QytEc0VpQ01uV2xlZC9F"
    "RDY2dmlJMFd6L0wrOXZ4az0iLAogICAgICAgICAgICAiWThROVFhVHBxUkFsU29Xbk53eVlHRGVy"
    "VkxMMWY2Yjhvc21iRFlFRjZvZz0iCiAgICAgICAgICBdLAogICAgICAgICAgImNoZWNrcG9pbnQi"
    "OiB7CiAgICAgICAgICAgICJlbnZlbG9wZSI6ICJsb2cyMDI1LWFscGhhMy5yZWtvci5zaWdzdGFn"
    "ZS5kZXZcbjQwMjY0NzlcbjVpYVg0eEs2UE1ucDRaRlRQNGl5d01jd0xnYVRqemdSVGtyVE9VRkly"
    "Zlk9XG5cbuKAlCBsb2cyMDI1LWFscGhhMy5yZWtvci5zaWdzdGFnZS5kZXYgMDlPbkRId1ZyS2VY"
    "akJZUTROSjVFb0VOZHRwQndaY1YzWDhuK2NBWkZJays4TEJQS0VwWmNLcW9OMkNmakd3MHpsUXd0"
    "K1UycEx0NVV5WUVGditTVFBIUG1RZz1cbuKAlCB3aXRuZXNzLnN0YWdlbW9sZS5ldSBaL2V1b0FB"
    "QUFBQnFCTSsyMW1WN0piSHdXbUxQMjBaU3BOV25lSE1rSVRPUG9wdCtWajFNb0pTSUxxT0lQcHg5"
    "M3BiUmZBMFUrRXE1SWlKakVVaEcxSndHaFBDSkVVVDV5RGNGRGc9PVxu4oCUIHN0YWdpbmcud2l0"
    "bmVzcy50cmFuc3BhcmVuY3kuZ29vZy9yaW5nLWFueS1iZWxscyBMaHFOeVFBQUFBQnFCTSsyT2I5"
    "RXRwRjloNklVTVhjL0xUUmtrWGlOQWdiYm9oQnhIZWtieTFvQVNkRHRQTGtnWkxtQkhINzNERjI1"
    "ZjJMOHhHZ3AvcVZ3b1pLNXRoS1hjNlhuQ3c9PVxu4oCUIHdpdG5lc3MubmF2aWdsaS5zdW5saWdo"
    "dC5nZW9teXMub3JnIG8rQVA0Z0FBQUFCcUJNKzJOb1lsSzRnM2xrQzVRbHREQjZmMkY5VTh5RjYr"
    "OGtYS2pMS0hBWXZTREozdTVkWnVmU1UzYnorQXRKTWRPb01peG9GSkJrUTM5N1dZL21FMGk5dGxD"
    "Zz09XG4iCiAgICAgICAgICB9CiAgICAgICAgfSwKICAgICAgICAiY2Fub25pY2FsaXplZEJvZHki"
    "OiAiZXlKaGNHbFdaWEp6YVc5dUlqb2lNQzR3TGpJaUxDSnJhVzVrSWpvaWFHRnphR1ZrY21WcmIz"
    "SmtJaXdpYzNCbFl5STZleUpvWVhOb1pXUlNaV3R2Y21SV01EQXlJanA3SW1SaGRHRWlPbnNpWVd4"
    "bmIzSnBkR2h0SWpvaVUwaEJNbDh5TlRZaUxDSmthV2RsYzNRaU9pSldMMGxVV0Vac1RXWTFUbWMx"
    "TlhCVlNYTkxVaXRKWlU1VGRWaEdUMkozUlROTFVtUndhakU0VEdoRlBTSjlMQ0p6YVdkdVlYUjFj"
    "bVVpT25zaVkyOXVkR1Z1ZENJNklrMUZWVU5KVVVOUlVURXhibk52VkdGUE5USXZUVU0wT0djd04y"
    "MXFRVlJrZUVRNE5ubDNaSFV6VG5oS1ZXcElOM2xCU1dkWFIyTXJUVkl6WlVsQmIxZDFPRXBGTVZG"
    "Tk1sbEVjMnh3YjBaQmVrMVdWM0JPTVRFMVprTkxkWEZuUFNJc0luWmxjbWxtYVdWeUlqcDdJbXRs"
    "ZVVSbGRHRnBiSE1pT2lKUVMwbFlYMFZEUkZOQlgxQXlOVFpmVTBoQlh6STFOaUlzSW5nMU1EbERa"
    "WEowYVdacFkyRjBaU0k2ZXlKeVlYZENlWFJsY3lJNklrMUpTVWxRYWtORFFqaFBaMEYzU1VKQlow"
    "bFZaVTB6VG14VGVtbEhjWGxNT1dWYVNUWXdiMXA0VEdJNWVtRm5kME5uV1VsTGIxcEplbW93UlVG"
    "M1RYZE9la1ZXVFVKTlIwRXhWVVZEYUUxTll6SnNibU16VW5aamJWVjFXa2RXTWsxU05IZElRVmxF"
    "VmxGUlJFVjRWbnBoVjJSNlpFYzVlVnBUTVhCaWJsSnNZMjB4YkZwSGJHaGtSMVYzU0doalRrMXFX"
    "WGRPVkVWNlRWUnJlVTE2VFhsWGFHTk9UV3BaZDA1VVJYcE5WR3Q2VFhwTmVWZHFRVUZOUm10M1JY"
    "ZFpTRXR2V2tsNmFqQkRRVkZaU1V0dldrbDZhakJFUVZGalJGRm5RVVV4VVhsSGRWaDJWelp4ZHk5"
    "Qk9GcEhhRkJOYTFSblRuUjFTVFZMUkRjNFRXVnNjbW95VFdsU1pYTTBVUzh6TlZsSkszUkRNVFJZ"
    "UzFscVVVUlJSR2RKVDFaUFJGbFlkRGswT0hOS2R6bDZSemRuV0UxclMwOURRblZKZDJkblltVk5R"
    "VFJIUVRGVlpFUjNSVUl2ZDFGRlFYZEpTR2RFUVZSQ1owNVdTRk5WUlVSRVFVdENaMmR5UW1kRlJr"
    "SlJZMFJCZWtGa1FtZE9Wa2hSTkVWR1oxRlZUWG94YjNKbWFYcHFNbUp0WlZSVFUwbExkVXM1ZW1n"
    "MWFWQmpkMGgzV1VSV1VqQnFRa0puZDBadlFWVmpXVmwzY0doU09GbHRMelU1T1dJd1FsSndMMWd2"
    "TDNKaU5uZDNaMkZWUjBFeFZXUkZVVVZDTDNkVFFtMXFRMEpzTkdGQ2JFZG9NR1JJUW5wUGFUaDJX"
    "akpzTUdGSVZtbE1iVTUyWWxNNWVtRlhaSHBrUnpsNVdsTXhhbUl5TlcxaU0wcDBXVmMxYWxwVE9X"
    "eGxTRko1V2xjeGJHSklhM1JhUjBaMVdqSldlV0l6Vm5wTVdFSXhXVzE0Y0ZsNU1YWmhWMUpxVEZk"
    "S2JGbFhUblppYVRoMVdqSnNNR0ZJVm1sTU0yUjJZMjEwYldKSE9UTmplVGxzWlVoU2VWcFhNV3hp"
    "U0d0MFdrZEdkVm95Vm5saU0xWjZURmM1Y0ZwSFRYUlpiVlpvV1RJNWRVeHViSFJpUlVKNVdsZGFl"
    "a3d5YUd4WlYxSjZUREl4YUdGWE5IZFBVVmxMUzNkWlFrSkJSMFIyZWtGQ1FWRlJjbUZJVWpCalNF"
    "MDJUSGs1TUdJeWRHeGlhVFZvV1ROU2NHSXlOWHBNYldSd1pFZG9NVmx1Vm5wYVdFcHFZakkxTUZw"
    "WE5UQk1iVTUyWWxSQlprSm5iM0pDWjBWRlFWbFBMMDFCUlVOQ1FrWXpZak5LY2xwdGVIWmtNVGxy"
    "WVZoT2QxbFlVbXBoUkVFeVFtZHZja0puUlVWQldVOHZUVUZGUkVKRFp6Vk9lbVJzVDBkUmQwOUVW"
    "VEZPUjA1b1QxZEthMWxVU1ROUFYwMDFUbTFWTlUxWFNteE5ha1Y0VDFkYWFVNXFaR3BPZWsxM1RV"
    "TXdSME5wYzBkQlVWRkNaemM0ZDBGUlVVVklNRlkwWkVoS2JHSlhWbk5sVTBKcldWYzFibHBZU25a"
    "a1dFMW5WREJzUlZGNVFtbGFWMFpxWWpJMGQxTlJXVXRMZDFsQ1FrRkhSSFo2UVVKQ1VWRTNZekpz"
    "Ym1NelVuWmpiVlYwV1RJNWRWcHRPWGxpVjBaMVdUSlZkbHBZYURCamJWWjBXbGQ0TlV4WFVtaGli"
    "V1JzWTIwNU1XTjVNWGRrVjBwellWZE5kR0l5Ykd0WmVURnBXbGRHYW1JeU5IZElVVmxMUzNkWlFr"
    "SkJSMFIyZWtGQ1FtZFJVR050Vm0xamVUbHZXbGRHYTJONU9YUlpWMngxVFVSelIwTnBjMGRCVVZG"
    "Q1p6YzRkMEZSWjBWTVVYZHlZVWhTTUdOSVRUWk1lVGt3WWpKMGJHSnBOV2haTTFKd1lqSTFla3h0"
    "WkhCa1IyZ3hXVzVXZWxwWVNtcGlNalV3V2xjMU1FeHRUblppVkVOQ2NHZFpTMHQzV1VKQ1FVZEVk"
    "bnBCUWtOUlUwSnNkM2xDYkVkb01HUklRbnBQYVRoMldqSnNNR0ZJVm1sTWJVNTJZbE01ZW1GWFpI"
    "cGtSemw1V2xNeGFtSXlOVzFpTTBwMFdWYzFhbHBUT1d4bFNGSjVXbGN4YkdKSWEzUmFSMFoxV2pK"
    "V2VXSXpWbnBNV0VJeFdXMTRjRmw1TVhaaFYxSnFURmRLYkZsWFRuWmlhVGgxV2pKc01HRklWbWxN"
    "TTJSMlkyMTBiV0pIT1ROamVUbHNaVWhTZVZwWE1XeGlTR3QwV2tkR2RWb3lWbmxpTTFaNlRGYzVj"
    "RnBIVFhSWmJWWm9XVEk1ZFV4dWJIUmlSVUo1V2xkYWVrd3lhR3haVjFKNlRESXhhR0ZYTkhkUFFW"
    "bExTM2RaUWtKQlIwUjJla0ZDUTJkUmNVUkRaelZPZW1Sc1QwZFJkMDlFVlRGT1IwNW9UMWRLYTFs"
    "VVNUTlBWMDAxVG0xVk5VMVhTbXhOYWtWNFQxZGFhVTVxWkdwT2VrMTNUVUl3UjBOcGMwZEJVVkZD"
    "WnpjNGQwRlJjMFZFZDNkT1dqSnNNR0ZJVm1sTVYyaDJZek5TYkZwRVFtVkNaMjl5UW1kRlJVRlpU"
    "eTlOUVVWTlFrWkJUVlJ0YURCa1NFSjZUMms0ZGxveWJEQmhTRlpwVEcxT2RtSlRPWHBoVjJSNlpF"
    "YzVlVnBUTVdwaU1qVnRZak5LZEZsWE5XcGFVemxzWlVoU2VWcFhNV3hpU0d0MFdrZEdkVm95Vm5s"
    "aU0xWjZURmhDTVZsdGVIQlplVEYyWVZkU2FreFhTbXhaVjA1MlltcEJORUpuYjNKQ1owVkZRVmxQ"
    "TDAxQlJVNUNRMjlOUzBSck0wNHlWVFJhUkVFMFRsUlZNRmt5UlRWWmJWSm9UV3BqTlZsNmF6SmFW"
    "R3Q0V1cxVmVVMVVSVFZhYlVreVRqSk5NMDE2UVhkSWQxbExTM2RaUWtKQlIwUjJla0ZDUkdkUlVr"
    "UkJPWGxhVjFwNlRESm9iRmxYVW5wTU1qRm9ZVmMwZDBkUldVdExkMWxDUWtGSFJIWjZRVUpFZDFG"
    "TVJFRnJNazE2U1RGUFZGazBUMVJqZDA1M1dVdExkMWxDUWtGSFJIWjZRVUpGUVZGd1JFTmtiMlJJ"
    "VW5kamVtOTJUREprY0dSSGFERlphVFZxWWpJd2RtTXliRzVqTTFKMlkyMVZkRmt5T1hWYWJUbDVZ"
    "bGRHZFZreVZYZEhVVmxMUzNkWlFrSkJSMFIyZWtGQ1JWRlJURVJCYTNoTmVrVTBUVVJSTVU1cVRY"
    "ZG5ZVmxIUTJselIwRlJVVUpuTnpoM1FWSkpSV2RhWTAxbldsSnZaRWhTZDJONmIzWk1NbVJ3WkVk"
    "b01WbHBOV3BpTWpCMll6SnNibU16VW5aamJWVjBXVEk1ZFZwdE9YbGlWMFoxV1RKVmRscFlhREJq"
    "YlZaMFdsZDROVXhYVW1oaWJXUnNZMjA1TVdONU1YZGtWMHB6WVZkTmRHSXliR3RaZVRGcFdsZEdh"
    "bUl5TkhaTWJXUndaRWRvTVZscE9UTmlNMHB5V20xNGRtUXpUWFphV0dnd1kyMVdkRnBYZURWTVYx"
    "Sm9ZbTFrYkdOdE9URmplVEYyWVZkU2FreFhTbXhaVjA1MlltazFOV0pYZUVGamJWWnRZM2s1YjFw"
    "WFJtdGplVGwwV1Zkc2RVMUVaMGREYVhOSFFWRlJRbWMzT0hkQlVrMUZTMmQzYjA5VVl6TmFWR2hy"
    "VFVSbk1VNVVVbXBaVkd4cFdrZEZlVTU2YkdwUFZGcHNUMVJHYVZwVVNYaE5WR3h0V1dwWk0xbDZZ"
    "M3BOUkVGb1FtZHZja0puUlVWQldVOHZUVUZGVlVKQ1RVMUZXR1IyWTIxMGJXSkhPVE5ZTWxKd1l6"
    "TkNhR1JIVG05TlNVZERRbWR2Y2tKblJVVkJXVTh2VFVGRlZrSklVVTFqYldnd1pFaENlazlwT0ha"
    "YU1td3dZVWhXYVV4dFRuWmlVemw2WVZka2VtUkhPWGxhVXpGcVlqSTFiV0l6U25SWlZ6VnFXbE01"
    "YkdWSVVubGFWekZzWWtocmRGcEhSblZhTWxaNVlqTldla3hZUWpGWmJYaHdXWGt4ZG1GWFVtcE1W"
    "MHBzV1ZkT2RtSnBPV2haTTFKd1lqSTFla3d6U2pGaWJrMTJUV3BWTkUxcVJYbFBSRWwzVGxScmRs"
    "bFlVakJhVnpGM1pFaE5kazFVUVZkQ1oyOXlRbWRGUlVGWlR5OU5RVVZYUWtGblRVSnVRakZaYlho"
    "d1dYcERRbXhSV1V0TGQxbENRa0ZJVjJWUlNVVkJaMU5DYUdkVFFtZDNRMEpCU0RoQlVHMUNlRlV6"
    "VW5WSFNreG5hMHhKTW5OVmJESktlVGx1ZEVVeGRtdHpOWFprZUVaTGRIbExaM0J5V1VGQlFVZGxT"
    "WE4wV210QlFVbEJRVUZHUVVGQlRqTm9VVVZCZDBKSlRVVlpRMGxSUXpJeGVtWlRMMnRQVEZSUVVE"
    "aDFkbE55UVVveU9EWllOR1IzTldKbFJqTndaME4wU0hBelJuTjJZa0ZKYUVGTlVqZDNkMVJYWmpr"
    "cksyUjBURkJ3VFd4U2JYWjZUME15WjNkblV6UnFPV1kzWlZKTWR6SjBlRmhOVFVGdlIwTkRjVWRU"
    "VFRRNVFrRk5SRUV5YTBGTlIxbERUVkZFYjFSV2JVOVVTekl6ZERWbFJTOHhRazF5TjBkUWVHbDVk"
    "a1l6SzBRNFdFb3ZUMnQ0TjFSU1NIVnBla3BxYkU1R05XUk9VbGRNV0ZKcmNEbHZhMFp5WTBOTlVV"
    "UlJjSEI1ZERoR01YUmlSbU00YlZGeGFYSjNRVGt3WlhsRlNtWkNWbXBITkVRd2JHOU9OblEzU25k"
    "TGNrOHJTeTlSZFc5Rk9FSXhiVzk2VkZwcGVFbGpQU0o5ZlgxOWZYMD0iCiAgICAgIH0KICAgIF0s"
    "CiAgICAidGltZXN0YW1wVmVyaWZpY2F0aW9uRGF0YSI6IHsKICAgICAgInJmYzMxNjFUaW1lc3Rh"
    "bXBzIjogWwogICAgICAgIHsKICAgICAgICAgICJzaWduZWRUaW1lc3RhbXAiOiAiTUlJQ3lUQURB"
    "Z0VBTUlJQ3dBWUpLb1pJaHZjTkFRY0NvSUlDc1RDQ0FxMENBUU14RFRBTEJnbGdoa2dCWlFNRUFn"
    "RXdnYmNHQ3lxR1NJYjNEUUVKRUFFRW9JR25CSUdrTUlHaEFnRUJCZ2tyQmdFRUFZTy9NQUl3TVRB"
    "TkJnbGdoa2dCWlFNRUFnRUZBQVFnalg1WGlvNHQrSUVhT0U1YmlHK2FETmsxdjQvQkF2VkZYZ0VJ"
    "UGs1djNmY0NGR0hYR2lPd1NiVGxOUnh0VEJXVnRpOU13QkFmR0E4eU1ESTJNRFV4TXpFNU1qTXpN"
    "MW93QXdJQkFhQXlwREF3TGpFVk1CTUdBMVVFQ2hNTWMybG5jM1J2Y21VdVpHVjJNUlV3RXdZRFZR"
    "UURFd3h6YVdkemRHOXlaUzEwYzJHZ0FER0NBZHN3Z2dIWEFnRUJNRkV3T1RFVk1CTUdBMVVFQ2hN"
    "TWMybG5jM1J2Y21VdVpHVjJNU0F3SGdZRFZRUURFeGR6YVdkemRHOXlaUzEwYzJFdGMyVnNabk5w"
    "WjI1bFpBSVVDaldoQm1IVjRrRnp4b21XcC9KOThuNERmS2N3Q3dZSllJWklBV1VEQkFJQm9JSDhN"
    "Qm9HQ1NxR1NJYjNEUUVKQXpFTkJnc3Foa2lHOXcwQkNSQUJCREFjQmdrcWhraUc5dzBCQ1FVeER4"
    "Y05Nall3TlRFek1Ua3lNek16V2pBdkJna3Foa2lHOXcwQkNRUXhJZ1Fnb2VXVElFNEwzV3psNGNS"
    "TGlwdjRPVUszSm9WSDdQZThQQjBJSjk4RHpQd3dnWTRHQ3lxR1NJYjNEUUVKRUFJdk1YOHdmVEI3"
    "TUhrRUlBYjAvK0JIL3JOWm1iY3pzTmVqSTFBYy9Camt3RE5tcUVYWGRUYm5TeWRFTUZVd1BhUTdN"
    "RGt4RlRBVEJnTlZCQW9UREhOcFozTjBiM0psTG1SbGRqRWdNQjRHQTFVRUF4TVhjMmxuYzNSdmNt"
    "VXRkSE5oTFhObGJHWnphV2R1WldRQ0ZBbzFvUVpoMWVKQmM4YUpscWZ5ZmZKK0EzeW5NQW9HQ0Nx"
    "R1NNNDlCQU1DQkdjd1pRSXhBT3BkQTg0ZTRYQUFEVEtMOWYrOTFvSWRybnpydHdxVkVYMUR2OTRa"
    "ZTJKaDJ1clRnOWExUzVpaXNxNkZXRGNvc3dJd0tHTkYwS1lwRDB2MUFLME01Uk05SzRBa2Y2dDJP"
    "K2dYdVV5bkc3bkJGRlRqMlUvRTlJYkY3aXVDaHcxemt5RVEiCiAgICAgICAgfQogICAgICBdCiAg"
    "ICB9CiAgfSwKICAiZHNzZUVudmVsb3BlIjogewogICAgInBheWxvYWQiOiAiZXlKZmRIbHdaU0k2"
    "SW1oMGRIQnpPaTh2YVc0dGRHOTBieTVwYnk5VGRHRjBaVzFsYm5RdmRqRWlMQ0p6ZFdKcVpXTjBJ"
    "anBiZXlKdVlXMWxJam9pWVM1MGVIUWlMQ0prYVdkbGMzUWlPbnNpYzJoaE1qVTJJam9pWVRCalpt"
    "TTNNVEkzTVdRMlpUSTNPR1UxTjJOa016TXlabVk1TlRkak0yWTNNRFF6Wm1Sa1lUTTFOR00wWTJK"
    "aU1Ua3dZVE13WkRVMlpXWmhNREZpWmlKOWZWMHNJbkJ5WldScFkyRjBaVlI1Y0dVaU9pSm9kSFJ3"
    "Y3pvdkwzTnNjMkV1WkdWMkwzQnliM1psYm1GdVkyVXZkakVpTENKd2NtVmthV05oZEdVaU9uc2lZ"
    "blZwYkdSRVpXWnBibWwwYVc5dUlqcDdJbUoxYVd4a1ZIbHdaU0k2SW1oMGRIQnpPaTh2WjJsMGFI"
    "VmlMbU52YlM5emFXZHpkRzl5WlMxamIyNW1iM0p0WVc1alpTOXlaV3R2Y2pJdFpITnpaUzFvWVhO"
    "b1pXUnlaV3R2Y21RdGNtVm5aVzRpZlN3aWNuVnVSR1YwWVdsc2N5STZleUppZFdsc1pHVnlJanA3"
    "SW1sa0lqb2lhSFIwY0hNNkx5OW5hWFJvZFdJdVkyOXRMM05wWjNOMGIzSmxMV052Ym1admNtMWhi"
    "bU5sTDNKbGEyOXlNaTFrYzNObExXaGhjMmhsWkhKbGEyOXlaQzF5WldkbGJpSjlmWDE5IiwKICAg"
    "ICJwYXlsb2FkVHlwZSI6ICJhcHBsaWNhdGlvbi92bmQuaW4tdG90bytqc29uIiwKICAgICJzaWdu"
    "YXR1cmVzIjogWwogICAgICB7CiAgICAgICAgInNpZyI6ICJNRVVDSVFDUVExMW5zb1RhTzUyL01D"
    "NDhnMDdtakFUZHhEODZ5d2R1M054SlVqSDd5QUlnV0djK01SM2VJQW9XdThKRTFRTTJZRHNscG9G"
    "QXpNVldwTjExNWZDS3VxZz0iCiAgICAgIH0KICAgIF0KICB9Cn0="
)


def _artifact_dsse() -> bytes:
    return base64.b64decode(_ARTIFACT_DSSE_B64)


def _artifact_dsse_bound() -> bytes:
    """The sigstore-conformance ``a.txt``: the artifact whose sha256
    matches the bundle's in-toto statement subject digest."""
    return base64.b64decode(_ARTIFACT_DSSE_BOUND_B64)


def _bundle_dsse_bytes() -> bytes:
    return base64.b64decode(_BUNDLE_DSSE_B64)


def _bundle_dsse_dict() -> dict:
    return json.loads(_bundle_dsse_bytes())


def _tampered_dsse_payload_bundle() -> bytes:
    """The DSSE bundle with its in-toto statement subject renamed.

    The payload bytes change while the envelope signature stays the
    same, so DSSE signature verification must reject it.
    """
    data = _bundle_dsse_dict()
    payload = json.loads(base64.b64decode(data["dsseEnvelope"]["payload"]))
    payload["subject"][0]["name"] = "b.txt"
    data["dsseEnvelope"]["payload"] = base64.b64encode(json.dumps(payload).encode("utf-8")).decode(
        "ascii"
    )
    return json.dumps(data).encode("utf-8")


def test_dsse_fixture_is_byte_identical_to_source():
    import hashlib

    assert hashlib.sha256(_artifact_dsse()).hexdigest() == _ARTIFACT_DSSE_SHA256
    assert hashlib.sha256(_bundle_dsse_bytes()).hexdigest() == _BUNDLE_DSSE_SHA256
    assert hashlib.sha256(_artifact_dsse_bound()).hexdigest() == _ARTIFACT_DSSE_BOUND_SHA256
    # The bound artifact is the one the statement was signed over.
    assert _ARTIFACT_DSSE_BOUND_SHA256 == _SUBJECT_DIGEST_DSSE


def test_dsse_bundle_parses_as_dsse_envelope():
    """The fixture is a genuine DSSE bundle, not a message-signature one."""
    from sigstore.models import Bundle

    bundle = Bundle.from_json(_bundle_dsse_bytes())
    assert bundle is not None
    raw = _bundle_dsse_dict()
    assert raw["mediaType"] == "application/vnd.dev.sigstore.bundle.v0.3+json"
    assert "dsseEnvelope" in raw
    assert "messageSignature" not in raw
    envelope = raw["dsseEnvelope"]
    assert envelope["payloadType"] == "application/vnd.in-toto+json"
    assert len(envelope["signatures"]) == 1
    payload = json.loads(base64.b64decode(envelope["payload"]))
    assert payload["_type"] == "https://in-toto.io/Statement/v1"
    assert payload["subject"] == [{"name": "a.txt", "digest": {"sha256": _SUBJECT_DIGEST_DSSE}}]
    assert payload["predicateType"] == "https://slsa.dev/provenance/v1"
    # Rekor v2 records DSSE envelopes as hashedrekord 0.0.2 entries.
    entry = raw["verificationMaterial"]["tlogEntries"][0]
    assert entry["kindVersion"] == {"kind": "hashedrekord", "version": "0.0.2"}


def test_dsse_bundle_verifies_offline_with_library_verifier(monkeypatch):
    """The real DSSE bundle verifies offline with the pinned sigstore library.

    This exercises the DSSE code path (certificate chain to the
    staging Fulcio root, expected identity and issuer, envelope
    signature, Rekor inclusion proof) that ``verify_sigstore_bundle``
    must dispatch to. It is here as fixture validation: the fixture
    is a genuine, verifiable DSSE bundle, and the pinned sigstore
    4.5.0 plus the shipped staging trust root can verify it with no
    network. The Puspa-level proof is test_dsse_bundle_verifies,
    which runs the same bundle through ``verify_sigstore_bundle``
    with the bound artifact.
    """
    from importlib.resources import as_file, files

    from sigstore.models import Bundle, TrustedRoot
    from sigstore.verify import Verifier
    from sigstore.verify.policy import Identity

    def _no_socket(*args, **kwargs):
        raise OSError("network disabled in test")

    monkeypatch.setattr(socket, "socket", _no_socket)
    bundle = Bundle.from_json(_bundle_dsse_bytes())
    resource = files("sigstore._store").joinpath(
        "https%3A%2F%2Ftuf-repo-cdn.sigstage.dev/trusted_root.json"
    )
    with as_file(resource) as path:
        root = TrustedRoot.from_file(str(path))
    payload_type, payload = Verifier(trusted_root=root).verify_dsse(
        bundle, Identity(identity=IDENTITY_DSSE, issuer=ISSUER_DSSE)
    )
    assert payload_type == "application/vnd.in-toto+json"
    assert payload == base64.b64decode(_bundle_dsse_dict()["dsseEnvelope"]["payload"])
    # Same expiry semantics as the message-signature fixture: the
    # certificate expired in 2026, yet the bundle verifies because
    # validity is judged at signing time.
    cert = x509.load_der_x509_certificate(
        base64.b64decode(_bundle_dsse_dict()["verificationMaterial"]["certificate"]["rawBytes"])
    )
    assert cert.not_valid_after_utc < datetime.now(UTC)
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert sans.get_values_for_type(x509.UniformResourceIdentifier) == [IDENTITY_DSSE]


def test_dsse_bundle_verifies():
    """A real DSSE bundle with its artifact, identity, and issuer verifies.

    The artifact is the sigstore-conformance ``a.txt``, whose sha256
    matches the in-toto statement's subject digest, so this pass
    proves the full DSSE path: certificate, identity, issuer,
    envelope signature, inclusion proof, and artifact-to-subject
    binding. (The sigstore-python companion artifact does NOT match
    the subject digest and is rejected by the binding check; see
    test_dsse_unbound_artifact_rejected.)
    """
    ok, reason = verify_sigstore_bundle(
        _bundle_dsse_bytes(),
        _artifact_dsse_bound(),
        IDENTITY_DSSE,
        ISSUER_DSSE,
        trust_root="staging",
    )
    assert (ok, reason) == (True, "ok")


def test_dsse_unbound_artifact_rejected():
    """The companion artifact is rejected: its digest is not a subject digest.

    The sigstore-python ``a.dsse.staging-rekor-v2.txt`` file
    (sha256 e248a5db...) is not what the bundle's in-toto statement
    was signed over (subject digest a0cfc712...). The envelope itself
    verifies, so this rejection must come from the binding check,
    not from the envelope: an attacker must not be able to present
    a genuine bundle for one artifact alongside a different one.
    """
    ok, reason = verify_sigstore_bundle(
        _bundle_dsse_bytes(),
        _artifact_dsse(),
        IDENTITY_DSSE,
        ISSUER_DSSE,
        trust_root="staging",
    )
    assert ok is False
    assert "subject digest" in reason


def _dsse_binding_statement(digest):
    """A minimal in-toto statement payload for the binding unit tests."""
    return json.dumps(
        {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "a.txt", "digest": {"sha256": digest}}],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {},
        }
    ).encode("utf-8")


@pytest.mark.parametrize(
    "payload_type,payload,artifact,expected",
    [
        # Matching digest passes the binding check.
        (
            "application/vnd.in-toto+json",
            _dsse_binding_statement(
                "a0cfc71271d6e278e57cd332ff957c3f7043fdda354c4cbb190a30d56efa01bf"
            ),
            _artifact_dsse_bound(),
            True,
        ),
        # A different artifact fails: the digest does not match.
        (
            "application/vnd.in-toto+json",
            _dsse_binding_statement(
                "a0cfc71271d6e278e57cd332ff957c3f7043fdda354c4cbb190a30d56efa01bf"
            ),
            _artifact_dsse(),
            False,
        ),
        # A statement whose subject digest is unrelated fails too.
        (
            "application/vnd.in-toto+json",
            _dsse_binding_statement("0" * 64),
            _artifact_dsse_bound(),
            False,
        ),
    ],
)
def test_dsse_binding_check_synthetic(payload_type, payload, artifact, expected):
    from puspa.policy.sigstore_verify import _check_dsse_binding

    ok, reason = _check_dsse_binding(payload_type, payload, artifact)
    assert ok is expected
    assert reason


@pytest.mark.parametrize(
    "payload_type,payload",
    [
        # Wrong payload type: not an in-toto statement.
        ("text/plain", _dsse_binding_statement("0" * 64)),
        # Not JSON at all.
        ("application/vnd.in-toto+json", b"not json"),
        # JSON but not an object.
        ("application/vnd.in-toto+json", b"[1, 2, 3]"),
        # Object but not an in-toto statement.
        (
            "application/vnd.in-toto+json",
            json.dumps({"_type": "https://example.com/Other/v1"}).encode("utf-8"),
        ),
        # Statement with no subject at all.
        (
            "application/vnd.in-toto+json",
            json.dumps(
                {
                    "_type": "https://in-toto.io/Statement/v1",
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "predicate": {},
                }
            ).encode("utf-8"),
        ),
        # Statement with an empty subject list.
        (
            "application/vnd.in-toto+json",
            json.dumps(
                {
                    "_type": "https://in-toto.io/Statement/v1",
                    "subject": [],
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "predicate": {},
                }
            ).encode("utf-8"),
        ),
        # Subject with no digest, and subject with a non-sha256 digest.
        (
            "application/vnd.in-toto+json",
            json.dumps(
                {
                    "_type": "https://in-toto.io/Statement/v1",
                    "subject": [{"name": "a.txt"}],
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "predicate": {},
                }
            ).encode("utf-8"),
        ),
        (
            "application/vnd.in-toto+json",
            json.dumps(
                {
                    "_type": "https://in-toto.io/Statement/v1",
                    "subject": [{"name": "a.txt", "digest": {"sha512": "ab" * 64}}],
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "predicate": {},
                }
            ).encode("utf-8"),
        ),
    ],
)
def test_dsse_binding_check_fails_closed(payload_type, payload):
    """Binding fails closed: no in-toto statement or no subject digest.

    None of these payloads may fall back to envelope-only proof.
    """
    from puspa.policy.sigstore_verify import _check_dsse_binding

    ok, reason = _check_dsse_binding(payload_type, payload, _artifact_dsse_bound())
    assert ok is False
    assert reason


def test_dsse_binding_check_accepts_any_listed_subject():
    """A statement listing several subjects binds any one of them."""
    from puspa.policy.sigstore_verify import _check_dsse_binding

    payload = json.dumps(
        {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {"name": "other.txt", "digest": {"sha256": "0" * 64}},
                {
                    "name": "a.txt",
                    "digest": {
                        "sha256": "a0cfc71271d6e278e57cd332ff957c3f7043fdda354c4cbb190a30d56efa01bf"
                    },
                },
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {},
        }
    ).encode("utf-8")
    ok, reason = _check_dsse_binding(
        "application/vnd.in-toto+json", payload, _artifact_dsse_bound()
    )
    assert (ok, reason) == (True, "ok")


# NOTE on the rejection tests below: each uses the bound artifact
# (``_artifact_dsse_bound``), so the artifact-to-subject binding
# holds and the rejection can only come from the tampering itself.
# Every test also asserts the reason is not a binding rejection
# ("subject digest" only appears in binding failures), which pins
# the "right reason" rather than just a rejection. One quirk worth
# knowing: the tampered-envelope-signature rejection surfaces as
# "not enough sources of verified time", because the bundle's
# RFC3161 timestamp is bound to the envelope signature, so flipping
# the signature breaks timestamp verification before the DSSE
# signature check runs. The tampering is still what rejects it.


def test_dsse_tampered_envelope_signature_rejected():
    data = _bundle_dsse_dict()
    sig = data["dsseEnvelope"]["signatures"][0]
    sig["sig"] = _flip_first_char(sig["sig"])
    ok, reason = verify_sigstore_bundle(
        json.dumps(data).encode("utf-8"),
        _artifact_dsse_bound(),
        IDENTITY_DSSE,
        ISSUER_DSSE,
        trust_root="staging",
    )
    assert ok is False
    assert reason
    assert "subject digest" not in reason


def test_dsse_tampered_payload_rejected():
    ok, reason = verify_sigstore_bundle(
        _tampered_dsse_payload_bundle(),
        _artifact_dsse_bound(),
        IDENTITY_DSSE,
        ISSUER_DSSE,
        trust_root="staging",
    )
    assert ok is False
    assert reason
    assert "subject digest" not in reason


def test_dsse_tampered_certificate_rejected():
    data = _bundle_dsse_dict()
    raw = data["verificationMaterial"]["certificate"]["rawBytes"]
    data["verificationMaterial"]["certificate"]["rawBytes"] = _flip_first_char(raw)
    ok, reason = verify_sigstore_bundle(
        json.dumps(data).encode("utf-8"),
        _artifact_dsse_bound(),
        IDENTITY_DSSE,
        ISSUER_DSSE,
        trust_root="staging",
    )
    assert ok is False
    assert reason
    assert "subject digest" not in reason


def test_dsse_tampered_inclusion_proof_rejected():
    data = _bundle_dsse_dict()
    proof = data["verificationMaterial"]["tlogEntries"][0]["inclusionProof"]
    proof["hashes"] = [_flip_first_char(proof["hashes"][0]), *proof["hashes"][1:]]
    ok, reason = verify_sigstore_bundle(
        json.dumps(data).encode("utf-8"),
        _artifact_dsse_bound(),
        IDENTITY_DSSE,
        ISSUER_DSSE,
        trust_root="staging",
    )
    assert ok is False
    assert reason
    assert "subject digest" not in reason


def test_dsse_wrong_identity_rejected():
    ok, reason = verify_sigstore_bundle(
        _bundle_dsse_bytes(),
        _artifact_dsse_bound(),
        "attacker@example.com",
        ISSUER_DSSE,
        trust_root="staging",
    )
    assert ok is False
    assert reason
    assert "subject digest" not in reason


def test_dsse_wrong_issuer_rejected():
    ok, reason = verify_sigstore_bundle(
        _bundle_dsse_bytes(),
        _artifact_dsse_bound(),
        IDENTITY_DSSE,
        "https://example.com",
        trust_root="staging",
    )
    assert ok is False
    assert reason
    assert "subject digest" not in reason


def test_dsse_staging_bundle_rejected_under_production_root():
    # Same non-interchangeability as the message-signature fixture.
    ok, reason = verify_sigstore_bundle(
        _bundle_dsse_bytes(),
        _artifact_dsse_bound(),
        IDENTITY_DSSE,
        ISSUER_DSSE,
        trust_root="production",
    )
    assert ok is False
    assert reason
    assert "subject digest" not in reason
