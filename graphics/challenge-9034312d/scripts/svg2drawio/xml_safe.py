from __future__ import annotations
import re
import xml.etree.ElementTree as _StdET
from pathlib import Path
from typing import BinaryIO
from xml.etree.ElementTree import Element,ParseError
try:
 import defusedxml.ElementTree as _DefusedET
 HAS_DEFUSEDXML=True
except ImportError:
 _DefusedET=None
 HAS_DEFUSEDXML=False
_FORBIDDEN_XML_DECL=re.compile(r"<!(?:DOCTYPE|ENTITY|ELEMENT|ATTLIST|NOTATION)\b",re.IGNORECASE)
def _assert_safe_xml_bytes(raw:bytes)->None:
 sample=raw[:32768].decode("utf-8",errors="ignore")
 if _FORBIDDEN_XML_DECL.search(sample):
  raise ValueError("Refused XML containing DOCTYPE/ENTITY declarations in stdlib fallback parser")
def safe_fromstring(data:str|bytes)->Element:
 if _DefusedET is not None:
  return _DefusedET.fromstring(data)
 raw=data.encode("utf-8",errors="ignore")if isinstance(data,str)else data
 _assert_safe_xml_bytes(raw)
 return _StdET.fromstring(data)
def safe_parse(source:str|Path)->_StdET.ElementTree:
 if _DefusedET is not None:
  return _DefusedET.parse(str(source))
 raw=Path(source).read_bytes()
 _assert_safe_xml_bytes(raw)
 return _StdET.ElementTree(_StdET.fromstring(raw))
