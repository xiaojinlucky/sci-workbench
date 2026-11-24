from __future__ import annotations
from.import geom_core as codes
from.geom_core import*
"""Shared atomic text-file write helper."""
import os
import tempfile
from os import path
def write_text_atomically(output_path:str,text:str)->None:
 output_dir=path.dirname(output_path)or "."
 fd,tmp_path=tempfile.mkstemp(dir=output_dir,prefix=".svg-to-drawio-",suffix=".tmp")
 try:
  with os.fdopen(fd,"w",encoding="utf-8")as handle:
   handle.write(text)
  os.replace(tmp_path,output_path)
 except BaseException:
  try:
   os.remove(tmp_path)
  except OSError:
   pass
  raise
"""In-memory draw.io cell model used before XML serialization."""
from collections.abc import Iterable
from dataclasses import dataclass,field
from xml.sax.saxutils import escape
def _xml_attr(value:object)->str:
 return escape(str(value),{'"':"&quot;"})
def _fmt_num(value:float|int)->str:
 return f"{float(value):.2f}"
@dataclass
class Point:
 x:float
 y:float
 def shift(self,dx:float,dy:float)->None:
  self.x-=dx
  self.y-=dy
 def to_xml(self,indent:str="        ",role:str|None=None)->str:
  attrs=[f'x="{_fmt_num(self.x)}"',f'y="{_fmt_num(self.y)}"']
  if role:
   attrs.append(f'as="{_xml_attr(role)}"')
  return f"{indent}<mxPoint {' '.join(attrs)}/>"
@dataclass
class Geometry:
 x:float|None=None
 y:float|None=None
 width:float|None=None
 height:float|None=None
 relative:bool=False
 source_point:Point|None=None
 target_point:Point|None=None
 points:list[Point]=field(default_factory=list)
 def bbox(self)->tuple[float,float,float,float]|None:
  pts:list[tuple[float,float]]=[]
  if None not in(self.x,self.y,self.width,self.height):
   assert self.x is not None and self.y is not None
   assert self.width is not None and self.height is not None
   pts.append((self.x,self.y))
   pts.append((self.x+self.width,self.y+self.height))
  for point in self.iter_points():
   pts.append((point.x,point.y))
  if not pts:
   return None
  xs=[point[0]for point in pts]
  ys=[point[1]for point in pts]
  return min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)
 def iter_points(self)->list[Point]:
  points:list[Point]=[]
  if self.source_point is not None:
   points.append(self.source_point)
  if self.target_point is not None:
   points.append(self.target_point)
  points.extend(self.points)
  return points
 def shift(self,dx:float,dy:float)->None:
  if self.x is not None:
   self.x-=dx
  if self.y is not None:
   self.y-=dy
  for point in self.iter_points():
   point.shift(dx,dy)
 def to_xml(self,indent:str="      ")->str:
  attrs:list[str]=[]
  if self.x is not None:
   attrs.append(f'x="{_fmt_num(self.x)}"')
  if self.y is not None:
   attrs.append(f'y="{_fmt_num(self.y)}"')
  if self.width is not None:
   attrs.append(f'width="{_fmt_num(self.width)}"')
  if self.height is not None:
   attrs.append(f'height="{_fmt_num(self.height)}"')
  if self.relative:
   attrs.append('relative="1"')
  attrs.append('as="geometry"')
  children:list[str]=[]
  if self.source_point is not None:
   children.append(self.source_point.to_xml(indent+"  ",role="sourcePoint"))
  if self.target_point is not None:
   children.append(self.target_point.to_xml(indent+"  ",role="targetPoint"))
  if self.points:
   points_xml="\n".join(point.to_xml(indent+"    ")for point in self.points)
   children.append(f'{indent}  <Array as="points">\n{points_xml}\n{indent}  </Array>')
  if not children:
   return f"{indent}<mxGeometry {' '.join(attrs)}/>"
  children_xml="\n".join(children)
  return f"{indent}<mxGeometry {' '.join(attrs)}>\n{children_xml}\n{indent}</mxGeometry>"
@dataclass
class Cell:
 id:str
 value:str=""
 style:str=""
 parent:str="1"
 vertex:bool=False
 edge:bool=False
 geometry:Geometry|None=None
 def bbox(self)->tuple[float,float,float,float]|None:
  if self.geometry is None:
   return None
  return self.geometry.bbox()
 def shift(self,dx:float,dy:float)->None:
  if self.geometry is not None:
   self.geometry.shift(dx,dy)
 def to_xml(self,indent:str="    ")->str:
  attrs=[f'id="{_xml_attr(self.id)}"',f'value="{_xml_attr(self.value)}"',f'style="{_xml_attr(self.style)}"',f'parent="{_xml_attr(self.parent)}"',]
  if self.vertex:
   attrs.append('vertex="1"')
  if self.edge:
   attrs.append('edge="1"')
  if self.geometry is None:
   return f"{indent}<mxCell {' '.join(attrs)}/>"
  return f"{indent}<mxCell {' '.join(attrs)}>\n{self.geometry.to_xml(indent+'  ')}\n{indent}</mxCell>"
def vertex_cell(cell_id:str,style:str,geometry:Geometry,value:str="",parent:str="1",)->Cell:
 return Cell(id=cell_id,value=value,style=style,parent=parent,vertex=True,geometry=geometry)
def edge_cell(cell_id:str,style:str,geometry:Geometry,value:str="",parent:str="1",)->Cell:
 return Cell(id=cell_id,value=value,style=style,parent=parent,edge=True,geometry=geometry)
def layer_cell(cell_id:str,label:str="")->Cell:
 return Cell(id=cell_id,value=label,style="",parent="1",vertex=True)
def group_bbox(cells:Iterable[Cell])->tuple[float,float,float,float]:
 points:list[tuple[float,float]]=[]
 for cell in cells:
  bbox=cell.bbox()
  if bbox:
   x,y,width,height=bbox
   points.append((x,y))
   points.append((x+width,y+height))
 if not points:
  return 0.0,0.0,1.0,1.0
 xs=[point[0]for point in points]
 ys=[point[1]for point in points]
 x=min(xs)
 y=min(ys)
 width=max(xs)-x or 1.0
 height=max(ys)-y or 1.0
 return x,y,width,height
def shift_cells(cells:Iterable[Cell],dx:float,dy:float)->None:
 for cell in cells:
  cell.shift(dx,dy)
def cells_to_xml(cells:Iterable[Cell],indent:str="    ")->str:
 return "\n".join(cell.to_xml(indent)for cell in cells)
"""XML serialization helpers for the final draw.io document."""
from collections.abc import Iterable
from xml.sax.saxutils import escape
def _graph_model_attrs(background:str|None)->str:
 if not background:
  return "<mxGraphModel>"
 safe_background=escape(background,{'"':"&quot;"})
 return f'<mxGraphModel background="{safe_background}">'
def _diagram_xml(title:str,cells:Iterable[Cell],background:str|None)->str:
 body=cells_to_xml(cells)
 safe_title=escape(title,{'"':"&quot;"})
 return(f'  <diagram name="{safe_title}">\n'f"    {_graph_model_attrs(background)}\n" "      <root>\n" '        <mxCell id="0"/>\n' '        <mxCell id="1" parent="0"/>\n' f"{body}\n" "      </root>\n" "    </mxGraphModel>\n" "  </diagram>")
def make_xml(cells:Iterable[Cell],title:str="Diagram",*,background:str|None=None)->str:
 return f"<mxfile>\n{_diagram_xml(title,cells,background)}\n</mxfile>\n"
def make_multi_diagram_xml(pages:Iterable[tuple[str,Iterable[Cell]]],*,background:str|None=None)->str:
 diagrams="\n".join(_diagram_xml(title,cells,background)for title,cells in pages)
 return f"<mxfile>\n{diagrams}\n</mxfile>\n"
"""Helpers for assembling draw.io style strings."""
from collections.abc import Iterable,Mapping
from typing import Self
StyleValue=str|int|float
StyleMappingValue=StyleValue|bool|None
class StyleBuilder:
 def __init__(self)->None:
  self._parts:list[str]=[]
 def add_flag(self,flag:str,*,when:bool=True)->Self:
  if when and flag:
   self._parts.append(flag)
  return self
 def add(self,key:str,value:StyleValue|None,*,when:bool=True)->Self:
  if when and value is not None:
   self._parts.append(f"{key}={value}")
  return self
 def extend_pairs(self,pairs:Mapping[str,StyleMappingValue]|Iterable[tuple[str,StyleMappingValue]],*,when:bool=True,)->Self:
  if not when:
   return self
  items=pairs.items()if isinstance(pairs,Mapping)else pairs
  for key,value in items:
   if value is None or value is False:
    continue
   if value is True:
    self.add_flag(key)
   else:
    self.add(key,value)
  return self
 def extend_raw(self,raw:str|None,*,when:bool=True)->Self:
  if not when or not raw:
   return self
  for part in raw.split(";"):
   part=part.strip()
   if part:
    self._parts.append(part)
  return self
 def build(self)->str:
  if not self._parts:
   return ""
  return ";".join(self._parts)+";"
"""Visual style extraction helpers shared by the element emitters."""
import re
from typing import TypedDict
from xml.etree.ElementTree import Element
class VisualStyle(TypedDict):
 fill:str|None
 stroke:str|None
 stroke_width:float
 opacity:float
 fill_opacity:float
 stroke_opacity:float
 font_size:float
 font_family:str
 text_anchor:str
 text_fill:str|None
 text_opacity:float
 font_weight:str
 font_style_v:str
 linecap:str
 linejoin:str
 dash_style:str
 marker_start:str|None
 marker_end:str|None
 marker_mid:str|None
 filter:str|None
 fill_rule:str
 text_decoration:str
 baseline_shift:str
 dominant_baseline:str
 letter_spacing:str
 text_length:float|None
 length_adjust:str
 stroke_miterlimit:str
class GradientStyle(TypedDict):
 color:str
 color2:str
 direction:str
 kind:str
 stops:list[GradientStop]
class GradientStop(TypedDict):
 offset:float
 color:str
_LINECAP:dict[str,str]={"butt":"flat","round":"round","square":"square"}
_LINEJOIN:dict[str,str]={"miter":"miter","round":"round","bevel":"bevel"}
_RGBA_RE=re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)" r"(?:\s*,\s*([^)]+))?\s*\)",re.IGNORECASE,)
_HSL_RE=re.compile(r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%" r"(?:\s*,\s*([^)]+))?\s*\)",re.IGNORECASE,)
def _hsl_to_rgb(hue:float,saturation:float,lightness:float)->tuple[int,int,int]:
 hue=hue%360
 chroma=(1-abs(2*lightness-1))*saturation
 second=chroma*(1-abs((hue/60)%2-1))
 match=lightness-chroma/2
 if hue<60:
  red,green,blue=chroma,second,0.0
 elif hue<120:
  red,green,blue=second,chroma,0.0
 elif hue<180:
  red,green,blue=0.0,chroma,second
 elif hue<240:
  red,green,blue=0.0,second,chroma
 elif hue<300:
  red,green,blue=second,0.0,chroma
 else:
  red,green,blue=chroma,0.0,second
 return(int((red+match)*255),int((green+match)*255),int((blue+match)*255),)
def _clamp01(value:float)->float:
 return max(0.0,min(1.0,value))
def _parse_alpha(value:str|None)->float:
 if value is None:
  return 1.0
 text=str(value).strip()
 if not text:
  return 1.0
 if text.endswith("%"):
  return _clamp01(parse_float(text[:-1])/100.0)
 return _clamp01(parse_float(text))
def _paint_with_alpha(color:str|None)->tuple[str|None,float]:
 if not color:
  return None,1.0
 text=str(color).strip()
 if text.lower()in("none","transparent"):
  return "none",1.0
 rgba_match=_RGBA_RE.match(text)
 if rgba_match:
  normalized=f"#{int(rgba_match.group(1)):02x}{int(rgba_match.group(2)):02x}{int(rgba_match.group(3)):02x}"
  return normalized,_parse_alpha(rgba_match.group(4))
 hsl_match=_HSL_RE.match(text)
 if hsl_match:
  rgb=_hsl_to_rgb(float(hsl_match.group(1)),float(hsl_match.group(2))/100,float(hsl_match.group(3))/100,)
  normalized=f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
  return normalized,_parse_alpha(hsl_match.group(4))
 short_hex_match=re.match(r"^#([0-9a-fA-F]{3})$",text)
 if short_hex_match:
  short_hex=short_hex_match.group(1)
  return "#{0}{0}{1}{1}{2}{2}".format(short_hex[0],short_hex[1],short_hex[2]),1.0
 if re.match(r"^#[0-9a-fA-F]{4}$",text):
  r_h,g_h,b_h,a_h=text[1],text[2],text[3],text[4]
  return f"#{r_h}{r_h}{g_h}{g_h}{b_h}{b_h}",_clamp01(int(a_h+a_h,16)/255.0)
 if re.match(r"^#[0-9a-fA-F]{8}$",text):
  return f"#{text[1:7].lower()}",_clamp01(int(text[7:9],16)/255.0)
 return text,1.0
def opacity_pct(value:float)->int:
 return int(round(_clamp01(value)*100))
def normalize_color(color:str|None)->str|None:
 return _paint_with_alpha(color)[0]
def get_visual(element:Element,computed_css:dict[str,str]|None=None)->VisualStyle:
 css=computed_css or{}
 def get_attr(name:str,default:str|None=None)->str|None:
  return css.get(name)or element.get(name)or default
 def resolve_color(value:str|None)->str|None:
  if value and value.strip().lower()=="currentcolor":
   return css.get("color")or element.get("color")or "#000000"
  return value
 dash_array=get_attr("stroke-dasharray")
 dash_style=""
 if dash_array and dash_array.lower()not in("none","0"):
  numbers=re.findall(r"[\d.]+",dash_array)
  if numbers:
   dash_style="dashed=1;dashPattern={};".format(" ".join(numbers))
 fill_raw=resolve_color(get_attr("fill","none"))
 stroke_raw=resolve_color(get_attr("stroke","none"))
 text_fill_raw=resolve_color(get_attr("fill","#000000"))
 fill,fill_alpha=_paint_with_alpha(fill_raw)
 stroke,stroke_alpha=_paint_with_alpha(stroke_raw)
 text_fill,text_alpha=_paint_with_alpha(text_fill_raw)
 opacity=_clamp01(parse_float(get_attr("opacity","1")))
 fill_opacity=_clamp01(parse_float(get_attr("fill-opacity","1"))*fill_alpha)
 stroke_opacity=_clamp01(parse_float(get_attr("stroke-opacity","1"))*stroke_alpha)
 text_opacity=_clamp01(parse_float(get_attr("fill-opacity","1"))*text_alpha)
 return{"fill":fill,"stroke":stroke,"stroke_width":parse_length(get_attr("stroke-width","1")),"opacity":opacity,"fill_opacity":fill_opacity,"stroke_opacity":stroke_opacity,"font_size":parse_float(re.sub(r"[^\d.]","",get_attr("font-size","12")or "12")),"font_family":get_attr("font-family","Helvetica")or "Helvetica","text_anchor":get_attr("text-anchor","start")or "start","text_fill":text_fill,"text_opacity":text_opacity,"font_weight":get_attr("font-weight","normal")or "normal","font_style_v":get_attr("font-style","normal")or "normal","linecap":_LINECAP.get(get_attr("stroke-linecap","butt")or "butt","flat"),"linejoin":_LINEJOIN.get(get_attr("stroke-linejoin","miter")or "miter","miter"),"dash_style":dash_style,"marker_start":get_attr("marker-start"),"marker_end":get_attr("marker-end"),"marker_mid":get_attr("marker-mid"),"filter":get_attr("filter"),"fill_rule":get_attr("fill-rule","nonzero")or "nonzero","text_decoration":get_attr("text-decoration","none")or "none","baseline_shift":get_attr("baseline-shift","0")or "0","dominant_baseline":get_attr("dominant-baseline","alphabetic")or "alphabetic","letter_spacing":get_attr("letter-spacing","normal")or "normal","text_length":parse_length(get_attr("textLength"))if get_attr("textLength")else None,"length_adjust":get_attr("lengthAdjust","spacing")or "spacing","stroke_miterlimit":get_attr("stroke-miterlimit","4")or "4",}
def gradient_entries(gradient:GradientStyle|None)->list[tuple[str,str|int]]:
 if not gradient:
  return[]
 return[("fillStyle",1),("gradientColor",gradient["color2"]),("gradientDirection",gradient["direction"]),]
def gradient_style(gradient:GradientStyle|None)->str:
 return "".join(f"{key}={value};" if value is not True else f"{key};" for key,value in gradient_entries(gradient))
def font_style_flag(style:VisualStyle)->int:
 bold=1 if style.get("font_weight")in("bold","700","800","900")else 0
 italic=2 if style.get("font_style_v")=="italic" else 0
 text_decoration=style.get("text_decoration","none")or "none"
 underline=4 if "underline" in text_decoration else 0
 strikethrough=8 if "line-through" in text_decoration else 0
 return bold|italic|underline|strikethrough
"""CSS parsing and cascade helpers used during SVG conversion."""
import re
from collections.abc import Collection,Sequence
from dataclasses import dataclass
from xml.etree.ElementTree import Element
Specificity=tuple[int,int,int]
@dataclass(frozen=True)
class AncestorInfo:
 tag:str
 elem_id:str
 classes:frozenset[str]
 element:Element
def ancestor_info(elem:Element)->AncestorInfo:
 return AncestorInfo(tag=strip_ns(elem.tag),elem_id=elem.get("id",""),classes=frozenset((elem.get("class")or "").split()),element=elem,)
_PRESENTATION_ATTR_SPECIFICITY:Specificity=(0,0,0)
_PRESENTATION_ATTR_ORDER=-1
_INHERITED_PRESENTATION_ATTRS:tuple[str,...]=("baseline-shift","color","dominant-baseline","fill","fill-opacity","fill-rule","font-family","font-size","font-style","font-weight","letter-spacing","stroke","stroke-dasharray","stroke-linecap","stroke-linejoin","stroke-miterlimit","stroke-opacity","stroke-width","text-anchor","text-decoration","visibility",)
@dataclass
class CssRule:
 selector:str
 props:dict[str,str]
 specificity:Specificity
 order:int
def _apply_presentation_attributes(elem:Element,computed:dict[str,str],winners:dict[str,tuple[Specificity,int]],custom_props:dict[str,str],)->None:
 for key in _INHERITED_PRESENTATION_ATTRS:
  value=elem.get(key)
  if value is None:
   continue
  resolved=_resolve_vars(str(value),custom_props)
  if resolved.strip().lower()=="inherit":
   continue
  computed[key]=resolved
  winners[key]=(_PRESENTATION_ATTR_SPECIFICITY,_PRESENTATION_ATTR_ORDER)
def extract_custom_props(css_rules:Sequence[CssRule])->dict[str,str]:
 custom:dict[str,str]={}
 for rule in css_rules:
  if rule.selector.strip()in(":root","html","*"):
   custom.update({key:value for key,value in rule.props.items()if key.startswith("--")})
 return custom
def _resolve_vars(value:str,custom_props:dict[str,str])->str:
 if not custom_props or "var(" not in value:
  return value
 def replace_var(match:re.Match[str])->str:
  name=match.group(1).strip()
  fallback=(match.group(2)or "").strip()
  return custom_props.get(name,fallback)
 return re.sub(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\s*\)",replace_var,value)
def _resolve_font_size(value:str,parent_px:float=12.0)->float:
 text=str(value).strip()
 if text.endswith("rem"):
  return parse_float(text[:-3])*16.0
 if text.endswith("em"):
  return parse_float(text[:-2])*parent_px
 if text.endswith("%"):
  return parse_float(text[:-1])/100.0*parent_px
 return parse_length(text,parent_px)
def _match_type_selector(selector:str,tag:str)->tuple[bool,str]:
 remainder=selector.strip()
 if not remainder or remainder=="*":
  return True,""
 if remainder==":root":
  return tag=="svg",""
 tag_match=re.match(r"^([a-zA-Z][a-zA-Z0-9-]*)",remainder)
 if tag_match:
  if tag_match.group(1)!=tag:
   return False,remainder
  remainder=remainder[tag_match.end():]
 return True,remainder
def _match_simple_sel(selector:str,tag:str,elem_id:str,elem_classes:Collection[str],elem:Element,)->bool:
 ok,remainder=_match_type_selector(selector,tag)
 if not ok:
  return False
 while remainder:
  if remainder[0]==".":
   match=re.match(r"^\.([\w-]+)",remainder)
   if not match or match.group(1)not in elem_classes:
    return False
   remainder=remainder[match.end():]
  elif remainder[0]=="#":
   match=re.match(r"^#([\w-]+)",remainder)
   if not match or match.group(1)!=elem_id:
    return False
   remainder=remainder[match.end():]
  elif remainder[0]=="[":
   end=remainder.find("]")
   if end<0:
    return False
   condition=remainder[:end+1]
   match=re.match(r'^\[([^\]~|^$*=]+?)(?:([~|^$*]?=)"?([^"\]]*)"?)?\]$',condition)
   if not match:
    return False
   attr_name,operator,required=match.group(1).strip(),match.group(2)or "",match.group(3)or ""
   elem_value=elem.get(attr_name)
   if elem_value is None:
    return False
   if operator=="=" and elem_value!=required:
    return False
   if operator=="~=" and required not in elem_value.split():
    return False
   if operator=="^=" and not elem_value.startswith(required):
    return False
   if operator=="$=" and not elem_value.endswith(required):
    return False
   if operator=="*=" and required not in elem_value:
    return False
   remainder=remainder[end+1:]
  else:
   return False
 return True
def _split_selector_chain(selector:str)->tuple[list[str],list[str]]:
 parts:list[str]=[]
 combinators:list[str]=[]
 buffer:list[str]=[]
 bracket_depth=0
 quote:str|None=None
 pending_descendant=False
 index=0
 def flush_part()->None:
  nonlocal buffer
  part="".join(buffer).strip()
  if part:
   parts.append(part)
  buffer=[]
 while index<len(selector):
  char=selector[index]
  if quote is not None:
   buffer.append(char)
   if char==quote:
    quote=None
   index+=1
   continue
  if char in{'"',"'"}and bracket_depth:
   quote=char
   buffer.append(char)
   index+=1
   continue
  if char=="[":
   bracket_depth+=1
   buffer.append(char)
   index+=1
   continue
  if char=="]" and bracket_depth:
   bracket_depth-=1
   buffer.append(char)
   index+=1
   continue
  if bracket_depth:
   buffer.append(char)
   index+=1
   continue
  if char.isspace():
   if buffer:
    flush_part()
    pending_descendant=True
   index+=1
   continue
  if char==">":
   if buffer:
    flush_part()
   pending_descendant=False
   if parts and len(combinators)<len(parts):
    combinators.append(">")
   index+=1
   while index<len(selector)and selector[index].isspace():
    index+=1
   continue
  if pending_descendant:
   if parts and len(combinators)<len(parts):
    combinators.append(" ")
   pending_descendant=False
  buffer.append(char)
  index+=1
 flush_part()
 if len(parts)!=len(combinators)+1:
  return[],[]
 return parts,combinators
def _info_matches(selector:str,info:AncestorInfo)->bool:
 return _match_simple_sel(selector,info.tag,info.elem_id,info.classes,info.element)
def _selector_matches(selector:str,tag:str,elem_id:str,elem_classes:set[str],elem:Element,ancestors:Sequence[AncestorInfo],)->bool:
 selector=selector.strip()
 if not selector:
  return False
 parts,combinators=_split_selector_chain(selector)
 if not parts:
  return False
 current=AncestorInfo(tag,elem_id,frozenset(elem_classes),elem)
 nodes=[*ancestors,current]
 node_index=len(nodes)-1
 if not _info_matches(parts[-1],nodes[node_index]):
  return False
 for part_index in range(len(parts)-2,-1,-1):
  combinator=combinators[part_index]
  if combinator==">":
   node_index-=1
   if node_index<0 or not _info_matches(parts[part_index],nodes[node_index]):
    return False
   continue
  matched_index:int|None=None
  for candidate_index in range(node_index-1,-1,-1):
   if _info_matches(parts[part_index],nodes[candidate_index]):
    matched_index=candidate_index
    break
  if matched_index is None:
   return False
  node_index=matched_index
 return True
def _selector_specificity(selector:str)->Specificity:
 id_count=0
 class_count=0
 tag_count=0
 for part in re.split(r"\s*>\s*|\s+",selector.strip()):
  if not part:
   continue
  id_count+=part.count("#")
  class_count+=part.count(".")
  class_count+=part.count("[")
  class_count+=part.count(":")
  tag_match=re.match(r"^([a-zA-Z][a-zA-Z0-9-]*)",part)
  if tag_match and tag_match.group(1)!="*":
   tag_count+=1
 return id_count,class_count,tag_count
def parse_css_rules(style_text:str,start_order:int=0)->tuple[list[CssRule],int]:
 rules:list[CssRule]=[]
 order=start_order
 cleaned_text=re.sub(r"/\*.*?\*/","",style_text,flags=re.DOTALL)
 for match in re.finditer(r"([^{]+)\{([^}]*)\}",cleaned_text):
  props=parse_style_attr(match.group(2))
  for raw_selector in match.group(1).split(","):
   selector=raw_selector.strip()
   if not selector:
    continue
   rules.append(CssRule(selector=selector,props=dict(props),specificity=_selector_specificity(selector),order=order,))
   order+=1
 return rules,order
def collect_css(svg_root:Element)->list[CssRule]:
 rules:list[CssRule]=[]
 order=0
 for elem in svg_root.iter():
  if strip_ns(elem.tag)!="style":
   continue
  text=(elem.text or "")+"".join(child.text or "" for child in elem)
  parsed_rules,order=parse_css_rules(text,start_order=order)
  rules.extend(parsed_rules)
 return rules
CssRuleIndex=dict[tuple[str,str],list[CssRule]]
_UNIVERSAL_INDEX_KEY:tuple[str,str]=("*","*")
def _subject_selector(selector:str)->str:
 selector=selector.strip()
 if ">" in selector:
  return selector.rsplit(">",1)[-1].strip()
 if " " in selector:
  return selector.rsplit(None,1)[-1].strip()
 return selector
def _subject_index_key(subject:str)->tuple[str,str]:
 id_match=re.search(r"#([\w-]+)",subject)
 if id_match:
  return("id",id_match.group(1))
 class_match=re.search(r"\.([\w-]+)",subject)
 if class_match:
  return("class",class_match.group(1))
 if subject==":root":
  return("tag","svg")
 tag_match=re.match(r"^([a-zA-Z][a-zA-Z0-9-]*)",subject)
 if tag_match:
  return("tag",tag_match.group(1))
 return _UNIVERSAL_INDEX_KEY
def index_css_rules(css_rules:Sequence[CssRule])->CssRuleIndex:
 index:CssRuleIndex={}
 for rule in css_rules:
  key=_subject_index_key(_subject_selector(rule.selector))
  index.setdefault(key,[]).append(rule)
 return index
def _candidate_rules(css_rules:Sequence[CssRule],rule_index:CssRuleIndex|None,tag:str,elem_id:str,elem_classes:set[str],)->Sequence[CssRule]:
 if rule_index is None:
  return css_rules
 candidates:list[CssRule]=list(rule_index.get(_UNIVERSAL_INDEX_KEY,()))
 candidates.extend(rule_index.get(("tag",tag),()))
 for elem_class in elem_classes:
  candidates.extend(rule_index.get(("class",elem_class),()))
 if elem_id:
  candidates.extend(rule_index.get(("id",elem_id),()))
 return candidates
def apply_css(elem:Element,css_rules:Sequence[CssRule],tag:str,inherited_styles:dict[str,str]|None=None,ancestors:Sequence[AncestorInfo]|None=None,*,custom_props:dict[str,str]|None=None,_match_cache:dict|None=None,rule_index:CssRuleIndex|None=None,)->dict[str,str]:
 inherited=inherited_styles or{}
 computed=dict(inherited)
 winners:dict[str,tuple[Specificity,int]]={}
 if custom_props is None:
  custom_props=extract_custom_props(css_rules)
 elem_id=elem.get("id","")
 elem_classes=set((elem.get("class")or "").split())
 ancestor_list=list(ancestors or[])
 _apply_presentation_attributes(elem,computed,winners,custom_props)
 element_custom_props=dict(custom_props or{})
 matched_rules:list[CssRule]=[]
 for rule in _candidate_rules(css_rules,rule_index,tag,elem_id,elem_classes):
  sel=rule.selector
  if _match_cache is not None and ">" not in sel and "[" not in sel and " " not in sel.strip():
   cache_key=(sel,tag,elem_id,frozenset(elem_classes))
   if cache_key not in _match_cache:
    _match_cache[cache_key]=_selector_matches(sel,tag,elem_id,elem_classes,elem,ancestor_list)
   if not _match_cache[cache_key]:
    continue
  elif not _selector_matches(sel,tag,elem_id,elem_classes,elem,ancestor_list):
   continue
  matched_rules.append(rule)
  for key,value in rule.props.items():
   if key.startswith("--"):
    element_custom_props[key]=value
 inline_props=parse_style_attr(elem.get("style",""))
 for key,value in inline_props.items():
  if key.startswith("--"):
   element_custom_props[key]=value
 for rule in matched_rules:
  for key,value in rule.props.items():
   if key.startswith("--"):
    continue
   resolved=_resolve_vars(str(value),element_custom_props)
   if resolved.strip().lower()=="inherit":
    continue
   current=winners.get(key,((-1,-1,-1),-1))
   candidate=(rule.specificity,rule.order)
   if candidate>=current:
    computed[key]=resolved
    winners[key]=candidate
 inline_order=len(css_rules)
 inline_specificity:Specificity=(10**6,0,0)
 for key,value in inline_props.items():
  if key.startswith("--"):
   continue
  resolved=_resolve_vars(str(value),element_custom_props)
  if resolved.strip().lower()=="inherit":
   continue
  computed[key]=resolved
  winners[key]=(inline_specificity,inline_order)
 if "font-size" in computed:
  parent_px=_resolve_font_size(inherited.get("font-size","12"),12.0)
  computed["font-size"]=str(_resolve_font_size(computed["font-size"],parent_px))
 return computed
"""Helpers for recognizing SVG filters that can map to native draw.io styles."""
from typing import Literal,TypedDict
from xml.etree.ElementTree import Element
class ShadowFilter(TypedDict):
 type:str
 dx:float
 dy:float
 color:str|None
 opacity:int
 approximation:Literal["native","approximate"]
 detail:str
_SUPPORTED_CLASSIC_SHADOW_PRIMITIVES:frozenset[str]=frozenset({"feGaussianBlur","feOffset","feFlood","feComposite","feMerge","feMergeNode","feColorMatrix","feComponentTransfer","feFuncA",})
def parse_shadow_filter(filter_elem:Element)->ShadowFilter|None:
 for child in filter_elem.iter():
  if strip_ns(child.tag)=="feDropShadow":
   return _from_drop_shadow(child)
 classic_shadow=_from_classic_shadow_chain(filter_elem)
 if classic_shadow is not None:
  return classic_shadow
 glow=_from_glow_chain(filter_elem)
 if glow is not None:
  return glow
 offset_only=_from_offset_chain(filter_elem)
 if offset_only is not None:
  return offset_only
 return None
def _from_drop_shadow(child:Element)->ShadowFilter:
 return{"type":"shadow","dx":parse_float(child.get("dx","2")),"dy":parse_float(child.get("dy","2")),"color":normalize_color(child.get("flood-color","#000000"))or "#000000","opacity":int(parse_float(child.get("flood-opacity","0.5"))*100),"approximation":"native","detail":"SVG drop shadows were mapped directly to native draw.io shadow styling.",}
def _from_classic_shadow_chain(filter_elem:Element)->ShadowFilter|None:
 primitives=[child for child in filter_elem if strip_ns(child.tag)]
 primitive_tags={strip_ns(child.tag)for child in primitives}
 if not primitive_tags or not primitive_tags.issubset(_SUPPORTED_CLASSIC_SHADOW_PRIMITIVES):
  return None
 offset=next((child for child in primitives if strip_ns(child.tag)=="feOffset"),None)
 flood=next((child for child in primitives if strip_ns(child.tag)=="feFlood"),None)
 has_composite=any(strip_ns(child.tag)=="feComposite" for child in primitives)
 has_merge=any(strip_ns(child.tag)=="feMerge" for child in primitives)
 if offset is None or flood is None or not(has_composite or has_merge):
  return None
 return{"type":"shadow","dx":parse_float(offset.get("dx","2")),"dy":parse_float(offset.get("dy","2")),"color":normalize_color(flood.get("flood-color","#000000"))or "#000000","opacity":int(parse_float(flood.get("flood-opacity","0.5"))*100),"approximation":"native","detail":"A classic blur-plus-offset shadow chain was mapped to native draw.io shadow styling.",}
def _from_glow_chain(filter_elem:Element)->ShadowFilter|None:
 primitives=[child for child in filter_elem if strip_ns(child.tag)]
 primitive_tags={strip_ns(child.tag)for child in primitives}
 supported={"feGaussianBlur","feFlood","feComposite","feMerge","feMergeNode","feColorMatrix"}
 if not primitive_tags or not primitive_tags.issubset(supported):
  return None
 if primitive_tags=={"feGaussianBlur"}:
  return None
 blur=next((child for child in primitives if strip_ns(child.tag)=="feGaussianBlur"),None)
 if blur is None:
  return None
 std_deviation=parse_float(blur.get("stdDeviation","0"))
 if std_deviation<=0 or std_deviation>3.5:
  return None
 if any(strip_ns(child.tag)=="feOffset" for child in primitives):
  return None
 flood=next((child for child in primitives if strip_ns(child.tag)=="feFlood"),None)
 color=normalize_color(flood.get("flood-color"))if flood is not None else None
 opacity=int(parse_float(flood.get("flood-opacity","0.35"))*100)if flood is not None else 35
 return{"type":"shadow","dx":0.0,"dy":0.0,"color":color,"opacity":opacity,"approximation":"approximate","detail":("A light blur or glow filter was approximated with a centered native shadow to keep the shape editable."),}
def _from_offset_chain(filter_elem:Element)->ShadowFilter|None:
 primitives=[child for child in filter_elem if strip_ns(child.tag)]
 primitive_tags={strip_ns(child.tag)for child in primitives}
 supported={"feOffset","feFlood","feComposite","feMerge","feMergeNode","feColorMatrix"}
 if not primitive_tags or not primitive_tags.issubset(supported):
  return None
 offset=next((child for child in primitives if strip_ns(child.tag)=="feOffset"),None)
 if offset is None:
  return None
 if any(strip_ns(child.tag)=="feGaussianBlur" for child in primitives):
  return None
 flood=next((child for child in primitives if strip_ns(child.tag)=="feFlood"),None)
 color=normalize_color(flood.get("flood-color"))if flood is not None else None
 opacity=int(parse_float(flood.get("flood-opacity","0.35"))*100)if flood is not None else 35
 return{"type":"shadow","dx":parse_float(offset.get("dx","2")),"dy":parse_float(offset.get("dy","2")),"color":color,"opacity":opacity,"approximation":"approximate","detail":"A simple offset filter was approximated with a native shadow so the element could stay editable.",}
"""Definition lookup helpers for gradients, markers, filters, and reusable SVG nodes."""
import math
import re
from dataclasses import dataclass
from xml.etree.ElementTree import Element
_MARKER_ID_MAP:dict[str,str]={"inhibit":"dash","tbar":"dash","blunt":"dash","repression":"dash","activate":"classic","phosphorylate":"classic","bind":"oval","complex":"oval","circle":"oval","dot":"oval","diamond":"diamond","transcribe":"classic","transport":"open","signal":"open","control":"open","waste":"open","exhaust":"open","feedback":"open","recycle":"open","open":"open","material":"block","optical":"block","arrow":"block","arrowhead":"block","triangle":"block",}
_POINT_RE=re.compile(r"[-\d.eE+]+")
@dataclass(frozen=True)
class FilterStyleResolution:
 entries:list[tuple[str,str|int]]
 approximated:bool
 detail:str
@dataclass(frozen=True)
class MarkerGeometry:
 arrow:str
 start_extension:float=0.0
 end_extension:float=0.0
 units:str="strokeWidth"
def _stop_color(stop_elem:Element)->str:
 props=parse_style_attr(stop_elem.get("style",""))
 raw=props.get("stop-color")or stop_elem.get("stop-color","#000000")
 color=normalize_color(raw)or "#000000"
 raw_opacity=props.get("stop-opacity")or stop_elem.get("stop-opacity","1")
 alpha=max(0.0,min(1.0,parse_float(raw_opacity,1.0)))
 if alpha<1.0 and color.startswith("#")and len(color)==7:
  red=int(color[1:3],16)
  green=int(color[3:5],16)
  blue=int(color[5:7],16)
  red=round(red*alpha+255*(1-alpha))
  green=round(green*alpha+255*(1-alpha))
  blue=round(blue*alpha+255*(1-alpha))
  color=f"#{red:02x}{green:02x}{blue:02x}"
 return color
def _get_href(elem:Element)->str|None:
 href=elem.get("href")or elem.get("{http://www.w3.org/1999/xlink}href")or ""
 return href[1:]if href.startswith("#")else None
def _parse_stop_offset(value:str|None)->float:
 text=(value or "0").strip()
 if text.endswith("%"):
  return max(0.0,min(1.0,parse_float(text[:-1])/100.0))
 return max(0.0,min(1.0,parse_float(text,0.0)))
def _parse_stops(elem:Element)->list[GradientStop]:
 stops:list[GradientStop]=[]
 for child in elem:
  if strip_ns(child.tag)=="stop":
   stops.append({"offset":_parse_stop_offset(child.get("offset")),"color":_stop_color(child)})
 stops.sort(key=lambda stop:stop["offset"])
 return stops
def _linear_direction(elem:Element)->str:
 x1=parse_float(elem.get("x1","0"))
 y1=parse_float(elem.get("y1","0"))
 x2=parse_float(elem.get("x2","1"))
 y2=parse_float(elem.get("y2","0"))
 dx,dy=x2-x1,y2-y1
 direction=("east" if dx>=0 else "west")if abs(dx)>=abs(dy)else("south" if dy>=0 else "north")
 gradient_transform=elem.get("gradientTransform","")
 if gradient_transform:
  pass
  matrix=parse_transform(gradient_transform)
  angle=math.degrees(math.atan2(matrix[1],matrix[0]))
  if-45<=angle<=45:
   direction="east"
  elif 45<angle<=135:
   direction="south"
  elif angle>135 or angle<-135:
   direction="west"
  else:
   direction="north"
 return direction
def _has_own_geometry(elem:Element)->bool:
 return any(elem.get(attr)is not None for attr in("x1","y1","x2","y2","gradientTransform"))
class DefsIndex:
 def __init__(self)->None:
  self._elements:dict[str,Element]={}
  self._element_transforms:dict[str,Matrix]={}
  self._gradients:dict[str,GradientStyle]={}
  self._markers:dict[str,MarkerGeometry]={}
  self._filters:dict[str,ShadowFilter]={}
 def index(self,svg_root:Element)->None:
  self._elements.clear()
  self._element_transforms.clear()
  self._gradients.clear()
  self._markers.clear()
  self._filters.clear()
  def walk(elem:Element,parent_matrix:Matrix)->None:
   local_matrix=mat_mul(parent_matrix,parse_transform(elem.get("transform")))
   tag=strip_ns(elem.tag)
   element_id=elem.get("id")
   if element_id:
    self._elements[element_id]=elem
    self._element_transforms[element_id]=local_matrix
   if tag=="linearGradient":
    self._index_linear(elem,element_id)
   elif tag=="radialGradient":
    self._index_radial(elem,element_id)
   elif tag=="marker":
    self._index_marker(elem,element_id)
   elif tag=="filter":
    self._index_filter(elem,element_id)
   for child in elem:
    walk(child,local_matrix)
  walk(svg_root,IDENTITY[:])
  for elem in svg_root.iter():
   tag=strip_ns(elem.tag)
   if tag not in("linearGradient","radialGradient"):
    continue
   element_id=elem.get("id")
   if not element_id or element_id in self._gradients:
    continue
   target_id=_get_href(elem)
   if not target_id or target_id not in self._gradients:
    continue
   parent=self._gradients[target_id]
   if tag=="linearGradient" and _has_own_geometry(elem):
    stops=parent["stops"]
    self._gradients[element_id]={"color":stops[0]["color"],"color2":stops[-1]["color"],"direction":_linear_direction(elem),"kind":"linear","stops":stops,}
   else:
    self._gradients[element_id]=parent
 def _index_linear(self,elem:Element,element_id:str|None)->None:
  stops=_parse_stops(elem)
  if not stops or not element_id:
   return
  self._gradients[element_id]={"color":stops[0]["color"],"color2":stops[-1]["color"],"direction":_linear_direction(elem),"kind":"linear","stops":stops,}
 def _index_radial(self,elem:Element,element_id:str|None)->None:
  stops=_parse_stops(elem)
  if not stops or not element_id:
   return
  self._gradients[element_id]={"color":stops[0]["color"],"color2":stops[-1]["color"],"direction":"radial","kind":"radial","stops":stops,}
 def _index_marker(self,elem:Element,element_id:str|None)->None:
  if not element_id:
   return
  lower_id=element_id.lower()
  marker_geometry=_marker_geometry(elem)
  for key,arrow in _MARKER_ID_MAP.items():
   if key in lower_id:
    self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,arrow)
    return
  for child in elem.iter():
   tag=strip_ns(child.tag)
   if tag=="circle":
    self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"oval")
    return
   if tag=="polygon":
    coords=_POINT_RE.findall(child.get("points",""))
    point_count=len(coords)//2
    if point_count==4 and "diamond" in lower_id:
     self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"diamond")
     return
    if point_count==3 and any(token in lower_id for token in("arrow","triangle","arrowhead")):
     self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"block")
     return
    self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"open")
    return
   if tag=="path":
    if "diamond" in lower_id:
     self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"diamond")
     return
    if any(token in lower_id for token in("arrow","triangle","arrowhead")):
     self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"block")
     return
    if _is_closed_triangle_path(child.get("d")):
     self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"block")
     return
    self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"open")
    return
   if tag=="rect" and any(token in lower_id for token in("square","box")):
    self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"block")
    return
  self._markers[element_id]=marker_geometry_with_arrow(marker_geometry,"open")
 def _index_filter(self,elem:Element,element_id:str|None)->None:
  if not element_id:
   return
  shadow=parse_shadow_filter(elem)
  if shadow is not None:
   self._filters[element_id]=shadow
 def get_element(self,ref_id:str)->Element|None:
  return self._elements.get(ref_id)
 def get_element_transform(self,ref_id:str)->Matrix|None:
  matrix=self._element_transforms.get(ref_id)
  return matrix[:]if matrix is not None else None
 def resolve_fill(self,fill_str:str|None)->tuple[str|None,GradientStyle|None]:
  if not fill_str:
   return fill_str,None
  match=re.match(r"url\(#([^)]+)\)",fill_str)
  if match:
   gradient=self._gradients.get(match.group(1))
   if gradient:
    return gradient["color"],gradient
  return fill_str,None
 def referenced_tag(self,paint_or_ref:str|None)->str|None:
  if not paint_or_ref:
   return None
  match=re.match(r"url\(#([^)]+)\)",paint_or_ref)
  if not match:
   return None
  elem=self._elements.get(match.group(1))
  return strip_ns(elem.tag)if elem is not None else None
 def resolve_marker(self,marker_str:str|None)->str:
  if not marker_str:
   return "none"
  match=re.match(r"url\(#([^)]+)\)",marker_str)
  if match:
   marker=self._markers.get(match.group(1))
   return marker.arrow if marker is not None else "open"
  return "none"
 def resolve_marker_extension(self,marker_str:str|None,*,at_start:bool,stroke_width:float,user_scale:float,)->float:
  if not marker_str:
   return 0.0
  match=re.match(r"url\(#([^)]+)\)",marker_str)
  if not match:
   return 0.0
  marker=self._markers.get(match.group(1))
  if marker is None:
   return 0.0
  extension=marker.start_extension if at_start else marker.end_extension
  scale=stroke_width if marker.units.lower()=="strokewidth" else user_scale
  return max(extension*scale,0.0)
 def resolve_custom_marker_shape(self,marker_str:str|None)->str|None:
  if not marker_str or self.resolve_marker(marker_str)!="open":
   return None
  match=re.match(r"url\(#([^)]+)\)",marker_str)
  if not match:
   return None
  marker=self._elements.get(match.group(1))
  if marker is None:
   return None
  return _simple_marker_shape(marker)
 def resolve_filter_style(self,filter_str:str|None,*,fallback_color:str|None=None,)->FilterStyleResolution|None:
  if not filter_str:
   return None
  match=re.match(r"url\(#([^)]+)\)",filter_str)
  if not match:
   return None
  shadow=self._filters.get(match.group(1))
  if shadow is None or shadow["type"]!="shadow":
   return None
  color=shadow["color"]or fallback_color or "#000000"
  return FilterStyleResolution(entries=[("shadow",1),("shadowColor",color),("shadowOpacity",shadow["opacity"]),("shadowOffsetX",f"{shadow['dx']:.0f}"),("shadowOffsetY",f"{shadow['dy']:.0f}"),],approximated=shadow["approximation"]=="approximate",detail=shadow["detail"],)
 def resolve_filter_entries(self,filter_str:str|None)->list[tuple[str,str|int]]:
  resolution=self.resolve_filter_style(filter_str)
  return resolution.entries if resolution is not None else[]
 def supports_filter(self,filter_str:str|None)->bool:
  if not filter_str:
   return True
  return self.resolve_filter_style(filter_str)is not None
 def resolve_filter(self,filter_str:str|None)->str:
  return "".join(f"{key}={value};" for key,value in self.resolve_filter_entries(filter_str))
def _simple_marker_shape(marker:Element)->str|None:
 marker_id=(marker.get("id")or "").lower()
 for child in marker:
  tag=strip_ns(child.tag)
  if tag=="circle":
   return "ellipse"
  if tag=="rect":
   return "square"
  if tag=="polygon":
   coords=_POINT_RE.findall(child.get("points",""))
   point_count=len(coords)//2
   if point_count==3:
    return "triangle"
   if point_count==4:
    return "diamond"
  if tag=="path":
   if any(tok in marker_id for tok in("open","transport","signal","control","waste","exhaust","feedback","recycle"))or(child.get("fill")or "").lower()=="none":
    return None
   if "diamond" in marker_id:
    return "diamond"
   if any(token in marker_id for token in("triangle","arrow","arrowhead")):
    return "triangle"
 return None
def marker_geometry_with_arrow(marker:MarkerGeometry,arrow:str)->MarkerGeometry:
 return MarkerGeometry(arrow=arrow,start_extension=marker.start_extension,end_extension=marker.end_extension,units=marker.units,)
def _marker_geometry(marker:Element)->MarkerGeometry:
 view_box=[parse_float(value)for value in re.split(r"[\s,]+",marker.get("viewBox","").strip())if value]
 marker_width=parse_float(marker.get("markerWidth"),3.0)
 marker_height=parse_float(marker.get("markerHeight"),3.0)
 ref_x=parse_float(marker.get("refX"),0.0)
 units=marker.get("markerUnits","strokeWidth")
 if len(view_box)>=4 and view_box[2]>0 and view_box[3]>0:
  min_x,_,width,height=view_box[:4]
  scale=min(marker_width/width,marker_height/height)
  return MarkerGeometry(arrow="open",start_extension=max(ref_x-min_x,0.0)*scale,end_extension=max(min_x+width-ref_x,0.0)*scale,units=units,)
 return MarkerGeometry(arrow="open",units=units)
def _is_closed_triangle_path(path_data:str|None)->bool:
 commands=path_commands(path_data or "")
 return(len(commands)==4 and commands[0][0]=="move" and commands[1][0]=="line" and commands[2][0]=="line" and commands[3][0]=="close")
"""Engine-level rendering policies that trade fidelity against editability."""
from dataclasses import dataclass
from typing import Literal,cast
GradientPolicy=Literal["auto","prefer-native","prefer-fallback"]
FilterPolicy=Literal["auto","prefer-native","force-fallback"]
TextMetricsPolicy=Literal["auto","system","heuristic"]
RenderingPresetKey=Literal["balanced","editability","fidelity"]
_GRADIENT_POLICIES:frozenset[str]=frozenset({"auto","prefer-native","prefer-fallback"})
_FILTER_POLICIES:frozenset[str]=frozenset({"auto","prefer-native","force-fallback"})
_TEXT_METRICS_POLICIES:frozenset[str]=frozenset({"auto","system","heuristic"})
_RENDERING_PRESETS:frozenset[str]=frozenset({"balanced","editability","fidelity"})
def _validate_choice(name:str,value:str,allowed:frozenset[str])->None:
 if value not in allowed:
  allowed_text=", ".join(sorted(allowed))
  raise ValueError(f"Invalid {name}: {value!r}. Expected one of: {allowed_text}.")
def normalize_filter_ref(filter_ref:str|None)->str|None:
 if not filter_ref:
  return None
 text=filter_ref.strip()
 if not text or text.lower()=="none":
  return None
 return text
def as_gradient_policy(value:str)->GradientPolicy:
 _validate_choice("gradient_policy",value,_GRADIENT_POLICIES)
 return cast(GradientPolicy,value)
def as_filter_policy(value:str)->FilterPolicy:
 _validate_choice("filter_policy",value,_FILTER_POLICIES)
 return cast(FilterPolicy,value)
def as_text_metrics_policy(value:str)->TextMetricsPolicy:
 _validate_choice("text_metrics_policy",value,_TEXT_METRICS_POLICIES)
 return cast(TextMetricsPolicy,value)
@dataclass(frozen=True)
class RenderingOptions:
 gradient_policy:GradientPolicy="auto"
 filter_policy:FilterPolicy="auto"
 text_metrics_policy:TextMetricsPolicy="auto"
 def __post_init__(self)->None:
  _validate_choice("gradient_policy",self.gradient_policy,_GRADIENT_POLICIES)
  _validate_choice("filter_policy",self.filter_policy,_FILTER_POLICIES)
  _validate_choice("text_metrics_policy",self.text_metrics_policy,_TEXT_METRICS_POLICIES)
 def to_dict(self)->dict[str,str]:
  return{"gradient_policy":self.gradient_policy,"filter_policy":self.filter_policy,"text_metrics_policy":self.text_metrics_policy,}
 def should_force_filter_fallback(self,filter_ref:str|None)->bool:
  return self.filter_policy=="force-fallback" and normalize_filter_ref(filter_ref)is not None
 def should_prefer_native_filter(self)->bool:
  return self.filter_policy=="prefer-native"
 def should_force_gradient_fallback(self)->bool:
  return self.gradient_policy=="prefer-fallback"
 def should_prefer_native_gradient(self)->bool:
  return self.gradient_policy=="prefer-native"
def rendering_preset_options(preset:str)->RenderingOptions:
 _validate_choice("rendering_preset",preset,_RENDERING_PRESETS)
 if preset=="editability":
  return RenderingOptions(gradient_policy="prefer-native",filter_policy="prefer-native",text_metrics_policy="heuristic",)
 if preset=="fidelity":
  return RenderingOptions(gradient_policy="prefer-fallback",filter_policy="force-fallback",text_metrics_policy="system",)
 return RenderingOptions()
def rendering_preset_label(preset:str)->str:
 _validate_choice("rendering_preset",preset,_RENDERING_PRESETS)
 return{"balanced":"Balanced","editability":"Best editability","fidelity":"Best visual fidelity",}[preset]
def detect_rendering_preset(options:RenderingOptions)->RenderingPresetKey|None:
 for preset in("balanced","editability","fidelity"):
  if rendering_preset_options(preset)==options:
   return cast(RenderingPresetKey,preset)
 return None
"""Central capability registry shared by compatibility summaries and UI help."""
from dataclasses import dataclass
from typing import Literal,cast
CompatibilityStatus=Literal["native","approximate","fallback","ignored"]
@dataclass(frozen=True)
class CapabilityDescriptor:
 key:str
 label:str
 description:str
 default_behavior:str
 editability_note:str
 fidelity_note:str
@dataclass(frozen=True)
class FeatureDefinition:
 key:str
 label:str
 description:str
 native_message:str
 approximate_message:str
 fallback_message:str
 ignored_message:str
 def message_for(self,status:CompatibilityStatus)->str:
  return{"native":self.native_message,"approximate":self.approximate_message,"fallback":self.fallback_message,"ignored":self.ignored_message,}[status]
@dataclass(frozen=True)
class CapabilityProfile:
 key:str
 capability:CapabilityDescriptor
 feature:FeatureDefinition
_STATUS_ORDER:dict[CompatibilityStatus,int]={"native":0,"approximate":1,"fallback":2,"ignored":3,}
_STATUS_LABELS:dict[CompatibilityStatus,str]={"native":"Editable","approximate":"Editable with simplification","fallback":"Embedded SVG fallback","ignored":"Not fully carried over",}
def _profile(key:str,*,label:str,description:str,default_behavior:str,editability_note:str,fidelity_note:str,native_message:str,approximate_message:str,fallback_message:str,ignored_message:str,)->CapabilityProfile:
 return CapabilityProfile(key=key,capability=CapabilityDescriptor(key=key,label=label,description=description,default_behavior=default_behavior,editability_note=editability_note,fidelity_note=fidelity_note,),feature=FeatureDefinition(key=key,label=label,description=description,native_message=native_message,approximate_message=approximate_message,fallback_message=fallback_message,ignored_message=ignored_message,),)
_PROFILES:dict[str,CapabilityProfile]={"shapes":_profile("shapes",label="Shapes and paths",description="Basic vectors such as rectangles, circles, polygons, and general paths.",default_behavior="Converted to native editable draw.io shapes whenever possible.",editability_note="Simple geometry stays editable; some transformed shapes may use stencil approximation.",fidelity_note="Very complex transformed geometry may still be baked into a stencil.",native_message="These shapes stayed as editable draw.io vectors.",approximate_message="These shapes stayed editable, but a few geometry details were simplified.",fallback_message="Some shapes were preserved as embedded SVG instead of editable vectors.",ignored_message="Some shapes could not be carried over correctly.",),"text":_profile("text",label="Text labels",description="SVG text, nested tspans, foreignObject labels, anchors, text measurements, and textPath layout.",default_behavior="Converted to editable draw.io text with automatic text measurement.",editability_note="Text stays editable even when some layout details must be approximated.",fidelity_note=("Qt, Pillow, or Tk font metrics improve sizing, while heuristic mode favors deterministic output."),native_message="Text stayed editable as draw.io text labels.",approximate_message="Text stayed editable, but some text layout details were approximated.",fallback_message="Some text was preserved as embedded SVG instead of editable text.",ignored_message="Some text layout features could not be carried over.",),"gradients":_profile("gradients",label="Gradients",description="Linear, radial, and multi-stop gradients.",default_behavior=("Auto mode keeps native gradients when the engine can approximate them well, " "otherwise it falls back to embedded SVG."),editability_note="Prefer-native keeps more output editable, but complex gradients may be simplified.",fidelity_note=("Prefer-fallback preserves the original look through embedded SVG when native gradients are not enough."),native_message="Gradients stayed editable using draw.io gradient styles.",approximate_message="Gradients stayed editable, but complex gradients were simplified.",fallback_message="Some gradients were preserved as embedded SVG for visual fidelity.",ignored_message="Some gradient information could not be carried over.",),"filters":_profile("filters",label="Filters and shadows",description="SVG filter effects such as shadows, blur, glow, and offsets.",default_behavior=("Native draw.io shadow styling is used when possible; unsupported filters can still fall back."),editability_note=("Prefer-native drops unsupported filters or approximates light effects to keep shapes editable."),fidelity_note="Force-fallback preserves filtered content visually, but those fragments are less editable.",native_message="Supported filters stayed editable with native draw.io styling.",approximate_message=("Some filter effects stayed editable, but were simplified to the closest native shadow/glow."),fallback_message="Some filter effects were preserved as embedded SVG.",ignored_message="Some filter effects could not be carried over.",),"clipping":_profile("clipping",label="Clip paths and masks",description="SVG clip paths and masking effects.",default_behavior="Simple cases may stay editable; more advanced clips and masks fall back to embedded SVG.",editability_note=("Simple circular, elliptical, rectangular, or polygonal clips can be rewritten into editable shapes."),fidelity_note="Complex clip and mask effects are preserved visually through embedded SVG fallback.",native_message="Clip and mask effects stayed editable.",approximate_message="Clip or mask effects stayed editable, but were simplified.",fallback_message="Clip or mask effects were preserved as embedded SVG.",ignored_message="Clip or mask effects could not be carried over.",),"patterns":_profile("patterns",label="Pattern fills",description="Repeated fills defined with `<pattern>`.",default_behavior=("Simple dot, stripe, and grid patterns may stay editable; more complex patterns fall back to embedded SVG."),editability_note="Simple repeating motifs can be expanded into editable draw.io geometry.",fidelity_note="SVG fallback keeps intricate pattern artwork visually intact.",native_message="Pattern fills stayed editable.",approximate_message="Pattern fills stayed editable, but were expanded into repeated native geometry.",fallback_message="Pattern fills were preserved as embedded SVG.",ignored_message="Pattern fills could not be carried over.",),"markers":_profile("markers",label="Arrowheads and markers",description="Marker-start, marker-mid, and marker-end decorations.",default_behavior="Mapped to the closest editable draw.io arrows when possible.",editability_note="Simple custom markers can be emitted as small editable endpoint shapes.",fidelity_note="Very custom marker artwork may still be approximated rather than matched exactly.",native_message="Markers stayed editable as draw.io arrows.",approximate_message="Markers stayed editable, but were matched to the closest draw.io arrows.",fallback_message="Markers were preserved visually through embedded SVG.",ignored_message="Some markers could not be carried over.",),"images":_profile("images",label="Images",description="Embedded, local, or linked raster/SVG image content.",default_behavior="Embedded as draw.io image cells.",editability_note="Images remain movable and resizable, but not vector-editable.",fidelity_note="Some skew-heavy transforms are approximated because draw.io images do not support true shear.",native_message="Images were kept as draw.io image cells.",approximate_message="Images were kept, but some transform or loading details were simplified.",fallback_message="Images were wrapped through SVG fallback for visual fidelity.",ignored_message="Some images could not be loaded or embedded safely.",),"references":_profile("references",label="Reused SVG content",description="Nested SVGs, symbols, and internal `<use>` references.",default_behavior="Expanded into editable draw.io content.",editability_note="Expanded content stays editable instead of remaining an opaque reference.",fidelity_note="Structure may be flattened compared to the original SVG reuse graph.",native_message="Reused SVG content stayed editable after expansion.",approximate_message="Reused SVG content stayed editable, but some structure was simplified.",fallback_message="Some reused SVG content was preserved as embedded SVG.",ignored_message="Some reused SVG content could not be carried over.",),"unsupported":_profile("unsupported",label="Unsupported SVG features",description="Features that are outside the current native engine coverage.",default_behavior=("Unsupported features are either approximated, preserved through fallback, or skipped when necessary."),editability_note="Approximation keeps more of the document editable, but some SVG semantics may be flattened.",fidelity_note="Fallback preserves the original look better when draw.io has no direct equivalent.",native_message="All encountered features were supported.",approximate_message="Some unsupported features were approximated.",fallback_message="Some unsupported features were preserved as embedded SVG.",ignored_message="Some unsupported features were skipped or only partially preserved.",),"limits":_profile("limits",label="Large-file limits",description="Truncation or safety limits applied during conversion.",default_behavior=("Safety limits protect the conversion from exploding output size or invalid fallback placement."),editability_note="When limits are not hit, output stays as editable as the engine allows.",fidelity_note="If a limit is hit, the engine favors a safe partial result over a broken document.",native_message="No conversion limits were triggered.",approximate_message="The file stayed editable with a few safety simplifications.",fallback_message="Some large fragments were preserved through fallback rendering.",ignored_message="The file hit a conversion limit, so some content was not emitted.",),}
_DEFAULT_PROFILE=_profile("other",label="Other SVG features",description="Miscellaneous SVG features noticed during conversion.",default_behavior="Handled case by case by the engine.",editability_note="Some features stay editable while others may need approximation.",fidelity_note="Fallback may be used when draw.io has no direct representation.",native_message="This content stayed editable in draw.io.",approximate_message="This content stayed editable, but the engine simplified it a little.",fallback_message="This content was preserved as embedded SVG for visual fidelity.",ignored_message="This content could not be fully carried over.",)
_PUBLIC_CAPABILITY_KEYS:tuple[str,...]=("shapes","text","gradients","filters","clipping","patterns","markers","images","references",)
_ISSUE_MAP:dict[str,tuple[str,CompatibilityStatus,str|None]]={codes.CLIP_PATH_SIMPLIFIED_NATIVE:("clipping","approximate","Simple clip paths were rewritten into editable replacement shapes.",),codes.MASK_SIMPLIFIED_NATIVE:("clipping","approximate","Simple masks were rewritten into editable replacement shapes.",),codes.PATTERN_SIMPLIFIED_NATIVE:("patterns","approximate","Simple repeating patterns were expanded into editable draw.io geometry.",),codes.CLIP_PATH_FALLBACK:("clipping","fallback","Clip paths were preserved as embedded SVG."),codes.MASK_FALLBACK:("clipping","fallback","Masks were preserved as embedded SVG."),codes.PATTERN_FALLBACK:("patterns","fallback","Pattern fills were preserved as embedded SVG."),codes.FILTER_FALLBACK:("filters","fallback","Filter effects were preserved as embedded SVG."),codes.FILTER_IGNORED_FOR_EDITABILITY:("filters","approximate","Unsupported filters were dropped to keep nearby shapes editable.",),codes.FILTER_SIMPLIFIED_NATIVE:("filters","approximate",None,),codes.MULTI_STOP_GRADIENT_FALLBACK:("gradients","fallback","Complex gradients were preserved as embedded SVG.",),codes.MULTI_STOP_GRADIENT_REDUCED:("gradients","approximate","Complex gradients were simplified to stay editable.",),codes.TEXT_BACKEND_HEURISTIC:("text","approximate","Text box sizes were estimated with the built-in heuristic.",),codes.TEXT_BACKEND_SYSTEM:("text","native","Text box sizes were measured with a real system font backend.",),codes.TEXT_PATH_APPROXIMATED:("text","approximate","Text on a path was approximated with rotated editable glyphs.",),codes.DOMINANT_BASELINE_APPROXIMATED:("text","approximate","Dominant-baseline alignment was approximated.",),codes.LETTER_SPACING_IGNORED:("text","ignored","Letter spacing is not preserved natively in draw.io text.",),codes.LETTER_SPACING_APPROXIMATED:("text","approximate","Letter spacing was approximated with editable positioned glyphs.",),codes.TEXT_LENGTH_APPROXIMATED:("text","approximate","Text length constraints were approximated with editable positioned glyphs.",),codes.FOREIGN_OBJECT_TEXT_APPROXIMATED:("text","approximate","HTML text inside foreignObject was flattened to editable draw.io text.",),codes.IMAGE_SHEAR_APPROXIMATED:("images","approximate","Sheared images were placed using their transformed bounding box.",),codes.IMAGE_REMOTE_LINKED:("images","approximate","Remote images stay linked instead of being embedded locally.",),codes.MAX_ELEMENTS_TRUNCATED:("limits","ignored","The element limit was reached, so the output was truncated.",),codes.FALLBACK_BOUNDS_MISSING:("limits","ignored","One SVG fallback fragment could not be positioned safely.",),codes.USE_CYCLE_DETECTED:("references","ignored","A circular <use> reference was detected and skipped.",),}
def capability_keys()->tuple[str,...]:
 return tuple(sorted(_PUBLIC_CAPABILITY_KEYS))
def capability_descriptor(capability_key:str)->CapabilityDescriptor|None:
 profile=_PROFILES.get(capability_key)
 if profile is None and capability_key==_DEFAULT_PROFILE.key:
  return _DEFAULT_PROFILE.capability
 return profile.capability if profile is not None else None
def all_capability_descriptors()->list[CapabilityDescriptor]:
 descriptors=[_PROFILES[key].capability for key in _PUBLIC_CAPABILITY_KEYS]
 return sorted(descriptors,key=lambda descriptor:descriptor.label.lower())
def feature_definition(feature_key:str)->FeatureDefinition:
 profile=_PROFILES.get(feature_key)
 return profile.feature if profile is not None else _DEFAULT_PROFILE.feature
def status_label(status:CompatibilityStatus)->str:
 return _STATUS_LABELS[status]
def merge_status(left:CompatibilityStatus,right:CompatibilityStatus)->CompatibilityStatus:
 return left if _STATUS_ORDER[left]>=_STATUS_ORDER[right]else right
def normalize_status(value:str)->CompatibilityStatus:
 if value in _STATUS_ORDER:
  return cast(CompatibilityStatus,value)
 return "native"
def issue_observation_payload(code:str,message:str,*,element_tag:str|None=None,)->tuple[str,CompatibilityStatus,str]|None:
 mapped=_ISSUE_MAP.get(code)
 if mapped is not None:
  feature_key,status,detail=mapped
  return feature_key,status,detail or message
 if code==codes.IGNORED_UNSUPPORTED_ELEMENT:
  detail=f"The SVG uses <{element_tag}> which is not supported natively." if element_tag else message
  return "unsupported","ignored",detail
 if code in{codes.CONVERSION_FAILED,codes.ANALYSIS_FAILED}:
  return "unsupported","ignored",message
 return None
"""User-facing compatibility summaries shared by the engine, CLI, and desktop app."""
from dataclasses import dataclass
@dataclass(frozen=True)
class FeatureObservation:
 feature_key:str
 status:CompatibilityStatus
 detail:str
 count:int=1
 def identity(self)->tuple[str,CompatibilityStatus,str]:
  return self.feature_key,self.status,self.detail
 def to_dict(self)->dict[str,str|int]:
  return{"feature_key":self.feature_key,"status":self.status,"detail":self.detail,"count":self.count,}
 @classmethod
 def from_dict(cls,payload:dict[str,object])->FeatureObservation:
  count=payload.get("count",1)
  if isinstance(count,bool):
   normalized_count=int(count)
  elif isinstance(count,int):
   normalized_count=count
  elif isinstance(count,float):
   normalized_count=int(count)
  elif isinstance(count,str):
   try:
    normalized_count=int(count)
   except ValueError:
    normalized_count=1
  else:
   normalized_count=1
  return cls(feature_key=str(payload.get("feature_key","")),status=normalize_status(str(payload.get("status","native"))),detail=str(payload.get("detail","")),count=max(1,normalized_count),)
@dataclass(frozen=True)
class CompatibilityRow:
 feature_key:str
 label:str
 description:str
 status:CompatibilityStatus
 status_label:str
 message:str
 count:int
 details:list[str]
 def to_dict(self)->dict[str,object]:
  return{"feature_key":self.feature_key,"label":self.label,"description":self.description,"status":self.status,"status_label":self.status_label,"message":self.message,"count":self.count,"details":list(self.details),}
@dataclass(frozen=True)
class CompatibilityOverview:
 level:str
 headline:str
 summary:str
 def to_dict(self)->dict[str,str]:
  return{"level":self.level,"headline":self.headline,"summary":self.summary,}
def observation_from_issue(code:str,message:str,*,element_tag:str|None=None,)->FeatureObservation|None:
 payload=issue_observation_payload(code,message,element_tag=element_tag)
 if payload is None:
  return None
 feature_key,status,detail=payload
 return FeatureObservation(feature_key=feature_key,status=status,detail=detail)
def observation_from_asset(*,status:str,message:str|None=None,mime_type:str|None=None,)->FeatureObservation|None:
 if status=="embedded":
  return FeatureObservation(feature_key="images",status="native",detail="Images were embedded into the output document.",)
 if status=="remote":
  detail=message or "Remote images stay linked instead of being embedded locally."
  return FeatureObservation(feature_key="images",status="approximate",detail=detail)
 if status in{"missing","rejected","invalid"}:
  detail=message or "An image could not be embedded safely."
  return FeatureObservation(feature_key="images",status="ignored",detail=detail)
 return None
def note_feature(feature_key:str,status:CompatibilityStatus,detail:str,)->FeatureObservation:
 return FeatureObservation(feature_key=feature_key,status=status,detail=detail)
def note_text_backend(backend:str)->FeatureObservation:
 if backend=="heuristic":
  return FeatureObservation(feature_key="text",status="approximate",detail="Text box sizes were estimated with the built-in heuristic.",)
 if backend=="qt":
  return FeatureObservation(feature_key="text",status="native",detail="Text box sizes were measured with Qt text shaping and font metrics.",)
 if backend=="pillow":
  return FeatureObservation(feature_key="text",status="native",detail="Text box sizes were measured with Pillow using a real font file.",)
 if backend=="tk":
  return FeatureObservation(feature_key="text",status="native",detail="Text box sizes were measured with Tk system font metrics.",)
 return FeatureObservation(feature_key="text",status="native",detail="Text box sizes were measured with a real font backend.",)
def note_gradient_usage(*,approximated:bool)->FeatureObservation:
 if approximated:
  return FeatureObservation(feature_key="gradients",status="approximate",detail="Complex gradients stayed editable through native approximation.",)
 return FeatureObservation(feature_key="gradients",status="native",detail="Gradients stayed editable with native draw.io gradient styles.",)
def note_filter_usage(*,native:bool,approximated:bool=False,detail:str|None=None,)->FeatureObservation:
 if approximated:
  return FeatureObservation(feature_key="filters",status="approximate",detail=detail or "SVG filters were simplified to the closest native draw.io shadow or glow.",)
 if native:
  return FeatureObservation(feature_key="filters",status="native",detail=detail or "Supported shadows were mapped to native draw.io styling.",)
 return FeatureObservation(feature_key="filters",status="fallback",detail=detail or "Filter effects were preserved through embedded SVG fallback.",)
def note_marker_usage()->FeatureObservation:
 return FeatureObservation(feature_key="markers",status="approximate",detail="SVG arrowheads were matched to the closest draw.io arrows.",)
def note_shape_usage(*,approximated:bool)->FeatureObservation:
 if approximated:
  return FeatureObservation(feature_key="shapes",status="approximate",detail="Some shapes were converted through baked geometry or stencil approximation.",)
 return FeatureObservation(feature_key="shapes",status="native",detail="Basic shapes and paths stayed as editable draw.io vectors.",)
def note_reference_usage()->FeatureObservation:
 return FeatureObservation(feature_key="references",status="native",detail="Reused SVG content was expanded into editable draw.io content.",)
def note_image_usage(*,approximated:bool)->FeatureObservation:
 if approximated:
  return FeatureObservation(feature_key="images",status="approximate",detail="Some image transforms were approximated to fit draw.io image cells.",)
 return FeatureObservation(feature_key="images",status="native",detail="Images were kept as draw.io image cells.",)
def build_compatibility_rows(observations:list[FeatureObservation],*,max_details:int=3,)->list[CompatibilityRow]:
 grouped:dict[str,list[FeatureObservation]]={}
 for observation in observations:
  grouped.setdefault(observation.feature_key,[]).append(observation)
 rows:list[CompatibilityRow]=[]
 for feature_key in sorted(grouped,key=lambda key:feature_definition(key).label.lower()):
  definition=feature_definition(feature_key)
  feature_observations=grouped[feature_key]
  status:CompatibilityStatus="native"
  total=0
  details:list[str]=[]
  for observation in feature_observations:
   status=merge_status(status,observation.status)
   total+=max(1,observation.count)
   if observation.detail and observation.detail not in details and len(details)<max_details:
    details.append(observation.detail)
  rows.append(CompatibilityRow(feature_key=feature_key,label=definition.label,description=definition.description,status=status,status_label=status_label(status),message=definition.message_for(status),count=total,details=details,))
 return rows
def build_compatibility_overview(rows:list[CompatibilityRow])->CompatibilityOverview:
 if not rows:
  return CompatibilityOverview(level="no-data",headline="No compatibility data yet.",summary="Run a conversion first to see how the SVG was handled.",)
 worst_status:CompatibilityStatus="native"
 for row in rows:
  worst_status=merge_status(worst_status,row.status)
 editable_count=sum(1 for row in rows if row.status=="native")
 approximate_count=sum(1 for row in rows if row.status=="approximate")
 fallback_count=sum(1 for row in rows if row.status=="fallback")
 ignored_count=sum(1 for row in rows if row.status=="ignored")
 if worst_status=="ignored":
  return CompatibilityOverview(level="limited",headline="Some SVG features could not be fully carried over.",summary=(f"{editable_count} feature area(s) stayed editable, "f"{approximate_count} were simplified, {fallback_count} used embedded SVG, "f"and {ignored_count} could not be fully preserved."),)
 if worst_status=="fallback":
  return CompatibilityOverview(level="mixed",headline="Most content converted, but some parts were preserved as embedded SVG.",summary=(f"{editable_count} feature area(s) stayed fully editable, "f"{approximate_count} were simplified, and {fallback_count} used SVG fallback."),)
 if worst_status=="approximate":
  return CompatibilityOverview(level="mostly-editable",headline="Everything stayed editable, with a few visual approximations.",summary=(f"{editable_count} feature area(s) stayed fully native and "f"{approximate_count} needed small simplifications."),)
 return CompatibilityOverview(level="fully-editable",headline="Everything in this SVG stayed editable in draw.io.",summary=f"All {editable_count} detected feature area(s) converted natively.",)
"""Structured diagnostics collected during one SVG conversion run."""
from dataclasses import dataclass,field
from datetime import UTC,datetime
REPORT_SCHEMA_VERSION=1
def _payload_int(payload:dict[str,object],key:str,default:int=0)->int:
 value=payload.get(key,default)
 if isinstance(value,bool):
  return int(value)
 if isinstance(value,int):
  return value
 if isinstance(value,float):
  return int(value)
 if isinstance(value,str):
  try:
   return int(value)
  except ValueError:
   return default
 return default
def _payload_list(payload:dict[str,object],key:str)->list[object]:
 value=payload.get(key,[])
 return value if isinstance(value,list)else[]
def _payload_float(payload:dict[str,object],key:str,default:float=0.0)->float:
 value=payload.get(key,default)
 if isinstance(value,bool):
  return float(value)
 if isinstance(value,(int,float)):
  return float(value)
 if isinstance(value,str):
  try:
   return float(value)
  except ValueError:
   return default
 return default
@dataclass(frozen=True)
class DiagnosticIssue:
 code:str
 severity:str
 message:str
 element_tag:str|None=None
 element_id:str|None=None
 fallback_used:bool=False
 def identity(self)->tuple[str,str|None,str|None,bool]:
  return self.code,self.element_tag,self.element_id,self.fallback_used
 def to_dict(self)->dict[str,str|bool|None]:
  return{"code":self.code,"severity":self.severity,"message":self.message,"element_tag":self.element_tag,"element_id":self.element_id,"fallback_used":self.fallback_used,}
 @classmethod
 def from_dict(cls,payload:dict[str,object])->DiagnosticIssue:
  return cls(code=str(payload.get("code","")),severity=str(payload.get("severity","warning")),message=str(payload.get("message","")),element_tag=str(payload["element_tag"])if payload.get("element_tag")is not None else None,element_id=str(payload["element_id"])if payload.get("element_id")is not None else None,fallback_used=bool(payload.get("fallback_used",False)),)
@dataclass(frozen=True)
class AssetReference:
 href:str
 status:str
 resolved_path:str|None=None
 mime_type:str|None=None
 message:str|None=None
 def identity(self)->tuple[str,str,str|None]:
  return self.href,self.status,self.resolved_path
 def to_dict(self)->dict[str,str|None]:
  return{"href":self.href,"status":self.status,"resolved_path":self.resolved_path,"mime_type":self.mime_type,"message":self.message,}
 @classmethod
 def from_dict(cls,payload:dict[str,object])->AssetReference:
  return cls(href=str(payload.get("href","")),status=str(payload.get("status","unknown")),resolved_path=str(payload["resolved_path"])if payload.get("resolved_path")is not None else None,mime_type=str(payload["mime_type"])if payload.get("mime_type")is not None else None,message=str(payload["message"])if payload.get("message")is not None else None,)
@dataclass(frozen=True)
class PreviewAnnotation:
 status:str
 label:str
 message:str
 x:float
 y:float
 width:float
 height:float
 feature_key:str|None=None
 element_tag:str|None=None
 element_id:str|None=None
 def identity(self)->tuple[str,str,str|None,str|None,str|None,float,float,float,float]:
  return(self.status,self.label,self.feature_key,self.element_tag,self.element_id,round(self.x,4),round(self.y,4),round(self.width,4),round(self.height,4),)
 def to_dict(self)->dict[str,str|float|None]:
  return{"status":self.status,"label":self.label,"message":self.message,"feature_key":self.feature_key,"x":self.x,"y":self.y,"width":self.width,"height":self.height,"element_tag":self.element_tag,"element_id":self.element_id,}
 @classmethod
 def from_dict(cls,payload:dict[str,object])->PreviewAnnotation:
  return cls(status=str(payload.get("status","approximate")),label=str(payload.get("label","")),message=str(payload.get("message","")),feature_key=str(payload["feature_key"])if payload.get("feature_key")is not None else None,x=_payload_float(payload,"x"),y=_payload_float(payload,"y"),width=_payload_float(payload,"width"),height=_payload_float(payload,"height"),element_tag=str(payload["element_tag"])if payload.get("element_tag")is not None else None,element_id=str(payload["element_id"])if payload.get("element_id")is not None else None,)
@dataclass
class ConversionReport:
 source_path:str=""
 output_path:str|None=None
 generated_at:str=field(default_factory=lambda:datetime.now(UTC).isoformat())
 analyze_only:bool=False
 cached:bool=False
 truncated:bool=False
 fallback_count:int=0
 emitted_cells:int=0
 issues:list[DiagnosticIssue]=field(default_factory=list)
 assets:list[AssetReference]=field(default_factory=list)
 preview_annotations:list[PreviewAnnotation]=field(default_factory=list)
 dependencies:list[str]=field(default_factory=list)
 feature_observations:list[FeatureObservation]=field(default_factory=list)
 def __post_init__(self)->None:
  self._issue_keys:set[tuple[str,str|None,str|None,bool]]={issue.identity()for issue in self.issues}
  self._asset_keys:set[tuple[str,str,str|None]]={asset.identity()for asset in self.assets}
  self._preview_annotation_keys:set[tuple[str,str,str|None,str|None,str|None,float,float,float,float]]={annotation.identity()for annotation in self.preview_annotations}
  self._dependency_keys:set[str]=set(self.dependencies)
  self._feature_observation_keys:dict[tuple[str,str,str],int]={observation.identity():index for index,observation in enumerate(self.feature_observations)}
 def clone(self)->ConversionReport:
  return ConversionReport.from_dict(self.to_dict())
 def add_issue(self,code:str,severity:str,message:str,*,element_tag:str|None=None,element_id:str|None=None,fallback_used:bool=False,)->None:
  issue=DiagnosticIssue(code=code,severity=severity,message=message,element_tag=element_tag,element_id=element_id,fallback_used=fallback_used,)
  issue_key=issue.identity()
  if issue_key in self._issue_keys:
   return
  self._issue_keys.add(issue_key)
  self.issues.append(issue)
  compatibility_observation=observation_from_issue(code,message,element_tag=element_tag)
  if compatibility_observation is not None:
   self.record_feature_observation(compatibility_observation)
 def add_asset(self,href:str,status:str,*,resolved_path:str|None=None,mime_type:str|None=None,message:str|None=None,)->None:
  asset=AssetReference(href=href,status=status,resolved_path=resolved_path,mime_type=mime_type,message=message,)
  asset_key=asset.identity()
  if asset_key in self._asset_keys:
   return
  self._asset_keys.add(asset_key)
  self.assets.append(asset)
  compatibility_observation=observation_from_asset(status=status,message=message,mime_type=mime_type)
  if compatibility_observation is not None:
   self.record_feature_observation(compatibility_observation)
 def add_preview_annotation(self,*,status:str,label:str,message:str,feature_key:str|None,x:float,y:float,width:float,height:float,element_tag:str|None=None,element_id:str|None=None,)->None:
  if width<=0.0 or height<=0.0:
   return
  annotation=PreviewAnnotation(status=status,label=label,message=message,feature_key=feature_key,x=x,y=y,width=width,height=height,element_tag=element_tag,element_id=element_id,)
  annotation_key=annotation.identity()
  if annotation_key in self._preview_annotation_keys:
   return
  self._preview_annotation_keys.add(annotation_key)
  self.preview_annotations.append(annotation)
 def add_dependency(self,dependency_path:str|None)->None:
  if not dependency_path:
   return
  if dependency_path in self._dependency_keys:
   return
  self._dependency_keys.add(dependency_path)
  self.dependencies.append(dependency_path)
 def record_feature_observation(self,observation:FeatureObservation)->None:
  observation_key=observation.identity()
  existing_index=self._feature_observation_keys.get(observation_key)
  if existing_index is not None:
   current=self.feature_observations[existing_index]
   self.feature_observations[existing_index]=FeatureObservation(feature_key=current.feature_key,status=current.status,detail=current.detail,count=current.count+max(1,observation.count),)
   return
  self._feature_observation_keys[observation_key]=len(self.feature_observations)
  self.feature_observations.append(observation)
 @property
 def warning_count(self)->int:
  return sum(1 for issue in self.issues if issue.severity=="warning")
 @property
 def error_count(self)->int:
  return sum(1 for issue in self.issues if issue.severity=="error")
 @property
 def compatibility_score(self)->int:
  score=100
  score-=self.fallback_count*12
  score-=self.warning_count*6
  score-=self.error_count*18
  score-=sum(8 for asset in self.assets if asset.status in{"missing","rejected","invalid"})
  if self.truncated:
   score-=20
  return max(0,score)
 @property
 def compatibility_matrix(self)->list[CompatibilityRow]:
  return build_compatibility_rows(self.feature_observations)
 @property
 def compatibility_overview(self)->CompatibilityOverview:
  return build_compatibility_overview(self.compatibility_matrix)
 def short_status(self)->str:
  parts=[f"score {self.compatibility_score}/100"]
  if self.fallback_count:
   parts.append(f"{self.fallback_count} fallback(s)")
  if self.warning_count:
   parts.append(f"{self.warning_count} warning(s)")
  if self.error_count:
   parts.append(f"{self.error_count} error(s)")
  if self.cached:
   parts.append("cache hit")
  if self.truncated:
   parts.append("truncated")
  return ", ".join(parts)
 def to_dict(self)->dict[str,object]:
  return{"schema_version":REPORT_SCHEMA_VERSION,"source_path":self.source_path,"output_path":self.output_path,"generated_at":self.generated_at,"analyze_only":self.analyze_only,"cached":self.cached,"truncated":self.truncated,"fallback_count":self.fallback_count,"emitted_cells":self.emitted_cells,"compatibility_score":self.compatibility_score,"issues":[issue.to_dict()for issue in self.issues],"assets":[asset.to_dict()for asset in self.assets],"preview_annotations":[annotation.to_dict()for annotation in self.preview_annotations],"dependencies":list(self.dependencies),"feature_observations":[observation.to_dict()for observation in self.feature_observations],"compatibility_overview":self.compatibility_overview.to_dict(),"compatibility_matrix":[row.to_dict()for row in self.compatibility_matrix],}
 @classmethod
 def from_dict(cls,payload:dict[str,object])->ConversionReport:
  return cls(source_path=str(payload.get("source_path","")),output_path=str(payload["output_path"])if payload.get("output_path")is not None else None,generated_at=str(payload.get("generated_at",datetime.now(UTC).isoformat())),analyze_only=bool(payload.get("analyze_only",False)),cached=bool(payload.get("cached",False)),truncated=bool(payload.get("truncated",False)),fallback_count=_payload_int(payload,"fallback_count"),emitted_cells=_payload_int(payload,"emitted_cells"),issues=[DiagnosticIssue.from_dict(item)for item in _payload_list(payload,"issues")if isinstance(item,dict)],assets=[AssetReference.from_dict(item)for item in _payload_list(payload,"assets")if isinstance(item,dict)],preview_annotations=[PreviewAnnotation.from_dict(item)for item in _payload_list(payload,"preview_annotations")if isinstance(item,dict)],dependencies=[str(item)for item in _payload_list(payload,"dependencies")if item is not None],feature_observations=[FeatureObservation.from_dict(item)for item in _payload_list(payload,"feature_observations")if isinstance(item,dict)],)
"""Rich conversion result objects shared by the public API and internal services."""
from dataclasses import dataclass
@dataclass(frozen=True)
class ConversionResult:
 xml:str
 report:ConversionReport
 source_path:str|None=None
 output_path:str|None=None
 @property
 def compatibility_score(self)->int:
  return self.report.compatibility_score
 @property
 def warning_count(self)->int:
  return self.report.warning_count
 @property
 def fallback_count(self)->int:
  return self.report.fallback_count
 def to_dict(self)->dict[str,object]:
  return{"source_path":self.source_path,"output_path":self.output_path,"xml":self.xml,"report":self.report.to_dict(),}
"""Lightweight post-conversion document hooks: page background and a notes legend.

Deliberately scoped to document-level/native draw.io attributes (the `mxGraphModel.background`
attribute and a plain legend layer) rather than rewriting every cell's style string into a
visual "theme" - that would require parsing and rewriting every emitted style, which is
invasive and fragile against already-custom styles.
"""
from dataclasses import dataclass
from xml.sax.saxutils import escape
_LEGEND_MARGIN=30.0
_LEGEND_HEIGHT=90.0
_LEGEND_WIDTH=320.0
_LEGEND_STYLE=("text;html=1;align=left;verticalAlign=top;whiteSpace=wrap;rounded=0;" "fillColor=#FFF9C4;strokeColor=#D4AC0D;spacing=8;")
@dataclass(frozen=True)
class PostProcessOptions:
 legend:bool=False
 background:str|None=None
 def to_dict(self)->dict[str,object]:
  return{"legend":self.legend,"background":self.background,}
 def is_noop(self)->bool:
  return not self.legend
def _legend_text(report:ConversionReport,title:str)->str:
 lines=[f"<b>{escape(title)}</b>",f"Compatibility score: {report.compatibility_score}/100",f"Warnings: {report.warning_count} &nbsp; Fallbacks: {report.fallback_count}",]
 if report.source_path:
  lines.append(escape(report.source_path))
 return "<br>".join(lines)
def _make_legend_cells(cells:list[Cell],report:ConversionReport,title:str,*,next_id:int)->list[Cell]:
 bbox=group_bbox(cells)
 x,y,_width,height=bbox
 legend_y=y+height+_LEGEND_MARGIN
 layer=layer_cell(str(next_id),"Notes")
 body=Cell(id=str(next_id+1),value=_legend_text(report,title),style=_LEGEND_STYLE,parent=layer.id,vertex=True,geometry=Geometry(x=x,y=legend_y,width=_LEGEND_WIDTH,height=_LEGEND_HEIGHT),)
 return[layer,body]
def apply_post_process(cells:list[Cell],report:ConversionReport,*,options:PostProcessOptions,title:str,)->list[Cell]:
 if options.is_noop():
  return cells
 result=list(cells)
 existing_ids={int(cell.id)for cell in result if cell.id.isdigit()}
 next_id=max(existing_ids,default=1)+1
 result.extend(_make_legend_cells(result,report,title,next_id=next_id))
 return result
