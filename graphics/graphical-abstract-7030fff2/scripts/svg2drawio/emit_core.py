from __future__ import annotations
from.geom_core import*
from.model_styles import*
"""Typed context passed to element emitters."""
from collections.abc import Callable,Sequence
from dataclasses import dataclass,field,replace
from typing import TYPE_CHECKING
if TYPE_CHECKING:
 pass
@dataclass(frozen=True)
class TraversalMode:
 allow_fallback:bool=True
 record_issues:bool=True
 enforce_max_elements:bool=True
@dataclass(frozen=True)
class EmitterContext:
 defs:DefsIndex
 parent_id:str
 link_url:str
 source_dir:str
 css_rules:Sequence[CssRule]
 custom_props:dict[str,str]
 report:ConversionReport
 rendering_options:RenderingOptions
 add_cell:Callable[[Cell],None]
 next_id_callback:Callable[[],str]
 rule_index:CssRuleIndex|None=field(default=None)
 mode:TraversalMode=field(default_factory=TraversalMode)
 def add(self,cell:Cell)->None:
  self.add_cell(cell)
 def next_id(self)->str:
  return self.next_id_callback()
 def with_parent(self,parent_id:str)->EmitterContext:
  return replace(self,parent_id=parent_id)
 def with_link(self,link_url:str)->EmitterContext:
  return replace(self,link_url=link_url)
 def with_mode(self,mode:TraversalMode)->EmitterContext:
  return replace(self,mode=mode)
"""Factory helpers for constructing draw.io cells and geometries."""
from collections.abc import Sequence
Point2D=tuple[float,float]
def bounds_geometry(x:float,y:float,width:float,height:float)->Geometry:
 return Geometry(x=x,y=y,width=width,height=height)
def edge_geometry(source:Point2D,target:Point2D,waypoints:Sequence[Point2D]=(),)->Geometry:
 return Geometry(relative=True,source_point=Point(source[0],source[1]),target_point=Point(target[0],target[1]),points=[Point(px,py)for px,py in waypoints],)
def make_vertex(ctx:EmitterContext,style:str,geometry:Geometry,*,value:str="",cell_id:str|None=None,parent_id:str|None=None,)->Cell:
 return vertex_cell(cell_id or ctx.next_id(),style,geometry,value=value,parent=parent_id or ctx.parent_id,)
def make_bounds_vertex(ctx:EmitterContext,style:str,x:float,y:float,width:float,height:float,*,value:str="",cell_id:str|None=None,parent_id:str|None=None,)->Cell:
 return make_vertex(ctx,style,bounds_geometry(x,y,width,height),value=value,cell_id=cell_id,parent_id=parent_id,)
def make_box_vertex(ctx:EmitterContext,style:str,box:BoundsBox,*,value:str="",cell_id:str|None=None,parent_id:str|None=None,)->Cell:
 return make_bounds_vertex(ctx,style,box.x,box.y,box.width,box.height,value=value,cell_id=cell_id,parent_id=parent_id,)
def make_layer_cell(ctx:EmitterContext,label:str="",cell_id:str|None=None)->Cell:
 return layer_cell(cell_id or ctx.next_id(),label)
def make_edge(ctx:EmitterContext,style:str,source:Point2D,target:Point2D,*,waypoints:Sequence[Point2D]=(),value:str="",cell_id:str|None=None,parent_id:str|None=None,)->Cell:
 return edge_cell(cell_id or ctx.next_id(),style,edge_geometry(source,target,waypoints),value=value,parent=parent_id or ctx.parent_id,)
"""Helpers for simple clip-path and mask rewrites that can stay editable."""
import re
from xml.etree.ElementTree import Element
SimpleBounds=tuple[float,float,float,float]
_SIMPLE_CLIP_TAGS:frozenset[str]=frozenset({"rect","circle","ellipse","polygon"})
def local_shape_bounds(elem:Element)->SimpleBounds|None:
 tag=strip_ns(elem.tag)
 if tag=="rect":
  x=parse_length(elem.get("x"))
  y=parse_length(elem.get("y"))
  width=parse_length(elem.get("width"))
  height=parse_length(elem.get("height"))
  return x,y,width,height
 if tag=="circle":
  cx=parse_length(elem.get("cx"))
  cy=parse_length(elem.get("cy"))
  radius=parse_length(elem.get("r"))
  return cx-radius,cy-radius,radius*2.0,radius*2.0
 if tag=="ellipse":
  cx=parse_length(elem.get("cx"))
  cy=parse_length(elem.get("cy"))
  rx=parse_length(elem.get("rx"))
  ry=parse_length(elem.get("ry"))
  return cx-rx,cy-ry,rx*2.0,ry*2.0
 if tag=="polygon":
  coords=[float(item)for item in re.findall(r"[-\d.eE+]+",elem.get("points",""))]
  if len(coords)<6:
   return None
  xs=coords[0::2]
  ys=coords[1::2]
  x=min(xs)
  y=min(ys)
  return x,y,max(xs)-x,max(ys)-y
 return None
def bounds_contains(outer:SimpleBounds,inner:SimpleBounds)->bool:
 ox,oy,ow,oh=outer
 ix,iy,iw,ih=inner
 epsilon=1e-6
 return ix>=ox-epsilon and iy>=oy-epsilon and ix+iw<=ox+ow+epsilon and iy+ih<=oy+oh+epsilon
def simple_clip_candidate(defs:DefsIndex,clip_ref:str,target_bounds:SimpleBounds)->Element|None:
 candidate,units=_simple_candidate(defs,clip_ref,expected_tag="clipPath",units_attr="clipPathUnits")
 if candidate is None or units is None:
  return None
 return _shape_in_target_units(candidate,units,target_bounds)
def simple_mask_candidate(defs:DefsIndex,mask_ref:str,target_bounds:SimpleBounds)->Element|None:
 candidate,units=_simple_candidate(defs,mask_ref,expected_tag="mask",units_attr="maskContentUnits")
 if candidate is None or units is None:
  return None
 fill=(candidate.get("fill")or "").strip().lower()
 if fill and fill not in{"#fff","#ffffff","white"}:
  return None
 return _shape_in_target_units(candidate,units,target_bounds)
def _simple_candidate(defs:DefsIndex,ref:str,*,expected_tag:str,units_attr:str,)->tuple[Element|None,str|None]:
 match=re.match(r"url\(#([^)]+)\)",ref)
 if not match:
  return None,None
 container=defs.get_element(match.group(1))
 if container is None or strip_ns(container.tag)!=expected_tag:
  return None,None
 units=(container.get(units_attr)or "userSpaceOnUse").strip()
 if units not in{"userSpaceOnUse","objectBoundingBox"}:
  return None,None
 drawable_children=[child for child in container if strip_ns(child.tag)in _SIMPLE_CLIP_TAGS]
 if len(drawable_children)!=1:
  return None,None
 return drawable_children[0],units
def _shape_in_target_units(candidate:Element,units:str,target_bounds:SimpleBounds)->Element|None:
 if units=="userSpaceOnUse":
  return candidate
 x,y,width,height=target_bounds
 tag=strip_ns(candidate.tag)
 if tag=="rect":
  attrib=dict(candidate.attrib)
  attrib["x"]=f"{x+parse_length(candidate.get('x'))*width:.6f}"
  attrib["y"]=f"{y+parse_length(candidate.get('y'))*height:.6f}"
  attrib["width"]=f"{parse_length(candidate.get('width'))*width:.6f}"
  attrib["height"]=f"{parse_length(candidate.get('height'))*height:.6f}"
  if candidate.get("rx")is not None:
   attrib["rx"]=f"{parse_length(candidate.get('rx'))*width:.6f}"
  if candidate.get("ry")is not None:
   attrib["ry"]=f"{parse_length(candidate.get('ry'))*height:.6f}"
  return Element(candidate.tag,attrib)
 if tag=="circle":
  cx=x+parse_length(candidate.get("cx"))*width
  cy=y+parse_length(candidate.get("cy"))*height
  radius=parse_length(candidate.get("r"))
  if abs(width-height)<=1e-6:
   return Element(candidate.tag,{**candidate.attrib,"cx":f"{cx:.6f}","cy":f"{cy:.6f}","r":f"{radius*width:.6f}",},)
  return Element("ellipse",{"cx":f"{cx:.6f}","cy":f"{cy:.6f}","rx":f"{radius*width:.6f}","ry":f"{radius*height:.6f}",},)
 if tag=="ellipse":
  return Element(candidate.tag,{**candidate.attrib,"cx":f"{x+parse_length(candidate.get('cx'))*width:.6f}","cy":f"{y+parse_length(candidate.get('cy'))*height:.6f}","rx":f"{parse_length(candidate.get('rx'))*width:.6f}","ry":f"{parse_length(candidate.get('ry'))*height:.6f}",},)
 if tag=="polygon":
  coords=[float(item)for item in re.findall(r"[-\d.eE+]+",candidate.get("points",""))]
  if len(coords)<6 or len(coords)%2!=0:
   return None
  scaled_points:list[str]=[]
  for px,py in zip(coords[0::2],coords[1::2],strict=True):
   scaled_points.append(f"{x+px*width:.6f},{y+py*height:.6f}")
  return Element(candidate.tag,{**candidate.attrib,"points":" ".join(scaled_points)})
 return None
"""Helpers for expanding very simple SVG patterns into editable draw.io geometry."""
import re
from dataclasses import replace
from xml.etree.ElementTree import Element
_PATTERN_MAX_CHILDREN=240
_EPSILON=1e-6
_SUPPORTED_PATTERN_TAGS:frozenset[str]=frozenset({"rect","line","circle"})
_IGNORED_PATTERN_TAGS:frozenset[str]=frozenset({"title","desc","metadata"})
def _resolve_pattern_dimension(raw:str|None,*,reference:float,is_bbox:bool)->float:
 if not is_bbox:
  return parse_length(raw)
 text=(raw or "").strip()
 if not text:
  return 0.0
 try:
  if text.endswith("%"):
   return float(text[:-1])/100.0*reference
  return float(text)*reference
 except ValueError:
  return 0.0
def emit_simple_pattern_fill(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str],)->bool:
 if strip_ns(elem.tag)!="rect":
  return False
 if normalize_filter_ref(css.get("filter")or elem.get("filter"))is not None:
  return False
 if(css.get("clip-path")or elem.get("clip-path")or "").strip().lower()not in{"","none"}:
  return False
 if(css.get("mask")or elem.get("mask")or "").strip().lower()not in{"","none"}:
  return False
 fill_value=css.get("fill")or elem.get("fill")or ""
 pattern_elem=_pattern_element(ctx,fill_value)
 if pattern_elem is None:
  return False
 pattern_units=(pattern_elem.get("patternUnits")or "objectBoundingBox").strip()
 if pattern_units not in{"userSpaceOnUse","objectBoundingBox"}:
  return False
 if(pattern_elem.get("patternContentUnits")or "userSpaceOnUse").strip()!="userSpaceOnUse":
  return False
 target_x=parse_length(elem.get("x"))
 target_y=parse_length(elem.get("y"))
 target_width=parse_length(elem.get("width"))
 target_height=parse_length(elem.get("height"))
 if target_width<=0 or target_height<=0:
  return False
 is_bbox_units=pattern_units=="objectBoundingBox"
 width=_resolve_pattern_dimension(pattern_elem.get("width"),reference=target_width,is_bbox=is_bbox_units)
 height=_resolve_pattern_dimension(pattern_elem.get("height"),reference=target_height,is_bbox=is_bbox_units)
 if width<=0 or height<=0:
  return False
 direct_pattern_tags=[strip_ns(child.tag)for child in pattern_elem]
 if any(tag not in _SUPPORTED_PATTERN_TAGS and tag not in _IGNORED_PATTERN_TAGS for tag in direct_pattern_tags):
  return False
 pattern_children=[child for child in pattern_elem if strip_ns(child.tag)in _SUPPORTED_PATTERN_TAGS]
 if not pattern_children:
  return False
 background=_background_rect(pattern_children,width,height)
 motif_children=[child for child in pattern_children if child is not background]
 if not motif_children:
  return False
 if not(_is_simple_line_pattern(motif_children)or _is_simple_dot_pattern(motif_children)):
  return False
 child_cells:list[Cell]=[]
 group_id=ctx.next_id()
 child_ctx=replace(ctx.with_parent(group_id),add_cell=child_cells.append)
 base_css=dict(css)
 if background is not None:
  background_css=dict(base_css)
  background_css["fill"]=_child_attr(background,"fill",default=background_css.get("fill","none"))
  background_css["stroke"]=base_css.get("stroke")or elem.get("stroke")or "none"
  background_css["stroke-width"]=base_css.get("stroke-width")or elem.get("stroke-width")or "1"
  emit_rect(child_ctx,elem,matrix,background_css)
 emitted_pattern_cells=_emit_pattern_tiles(child_ctx,elem,matrix,motif_children,base_css,target_x,target_y,target_width,target_height,tile_width=width,tile_height=height,)
 if emitted_pattern_cells<=0:
  return False
 _emit_pattern_boundary_lines(child_ctx,elem,matrix,motif_children,base_css,target_x,target_y,target_width,target_height,tile_width=width,tile_height=height,)
 direct_children=[cell for cell in child_cells if cell.parent==group_id]
 if not direct_children:
  return False
 gx,gy,gw,gh=group_bbox(direct_children)
 ctx.add(make_bounds_vertex(ctx,"group;",gx,gy,gw,gh,cell_id=group_id))
 shift_cells(direct_children,gx,gy)
 for cell in child_cells:
  ctx.add(cell)
 ctx.report.add_issue(PATTERN_SIMPLIFIED_NATIVE,"warning","A simple repeating SVG pattern was expanded into editable draw.io geometry.",element_tag=strip_ns(elem.tag),element_id=elem.get("id"),)
 return True
def _emit_pattern_tiles(ctx:EmitterContext,elem:Element,matrix:Matrix,motif_children:list[Element],base_css:dict[str,str],target_x:float,target_y:float,target_width:float,target_height:float,*,tile_width:float,tile_height:float,)->int:
 emitted=0
 x_repeat_count=int((target_width+_EPSILON)//tile_width)+2
 y_repeat_count=int((target_height+_EPSILON)//tile_height)+2
 if len(motif_children)*x_repeat_count*y_repeat_count>_PATTERN_MAX_CHILDREN:
  return 0
 tile_origin_x=target_x
 tile_origin_y=target_y
 for y_index in range(y_repeat_count):
  offset_y=tile_origin_y+y_index*tile_height
  for x_index in range(x_repeat_count):
   offset_x=tile_origin_x+x_index*tile_width
   for child in motif_children:
    tag=strip_ns(child.tag)
    if tag=="line":
     line=_translated_line(child,offset_x,offset_y)
     if _line_outside_rect(line,target_x,target_y,target_width,target_height):
      continue
     emit_line(ctx,line,matrix,_pattern_child_css(base_css,child))
     emitted+=1
    elif tag=="circle":
     circle=_translated_circle(child,offset_x,offset_y)
     if _circle_outside_rect(circle,target_x,target_y,target_width,target_height):
      continue
     emit_circle(ctx,circle,matrix,_pattern_child_css(base_css,child))
     emitted+=1
 return emitted
def _emit_pattern_boundary_lines(ctx:EmitterContext,elem:Element,matrix:Matrix,motif_children:list[Element],base_css:dict[str,str],target_x:float,target_y:float,target_width:float,target_height:float,*,tile_width:float,tile_height:float,)->None:
 if not _is_simple_line_pattern(motif_children):
  return
 has_horizontal=any(abs(parse_length(child.get("y1"))-parse_length(child.get("y2")))<=_EPSILON for child in motif_children)
 has_vertical=any(abs(parse_length(child.get("x1"))-parse_length(child.get("x2")))<=_EPSILON for child in motif_children)
 width_remainder=target_width%tile_width
 height_remainder=target_height%tile_height
 if has_vertical and width_remainder>_EPSILON:
  vertical=next(child for child in motif_children if abs(parse_length(child.get("x1"))-parse_length(child.get("x2")))<=_EPSILON)
  emit_line(ctx,Element("line",{**vertical.attrib,"x1":f"{target_x+target_width:.6f}","x2":f"{target_x+target_width:.6f}","y1":f"{target_y:.6f}","y2":f"{target_y+target_height:.6f}",},),matrix,_pattern_child_css(base_css,vertical),)
 if has_horizontal and height_remainder>_EPSILON:
  horizontal=next(child for child in motif_children if abs(parse_length(child.get("y1"))-parse_length(child.get("y2")))<=_EPSILON)
  emit_line(ctx,Element("line",{**horizontal.attrib,"x1":f"{target_x:.6f}","x2":f"{target_x+target_width:.6f}","y1":f"{target_y+target_height:.6f}","y2":f"{target_y+target_height:.6f}",},),matrix,_pattern_child_css(base_css,horizontal),)
def _pattern_element(ctx:EmitterContext,fill_value:str)->Element|None:
 match=re.match(r"url\(#([^)]+)\)",fill_value)
 if not match:
  return None
 pattern_elem=ctx.defs.get_element(match.group(1))
 if pattern_elem is None or strip_ns(pattern_elem.tag)!="pattern":
  return None
 return pattern_elem
def _background_rect(children:list[Element],width:float,height:float)->Element|None:
 for child in children:
  if strip_ns(child.tag)!="rect":
   continue
  x=parse_length(child.get("x"))
  y=parse_length(child.get("y"))
  child_width=parse_length(child.get("width"))
  child_height=parse_length(child.get("height"))
  if(abs(x)<=_EPSILON and abs(y)<=_EPSILON and child_width>=width-_EPSILON and child_height>=height-_EPSILON):
   return child
 return None
def _is_simple_line_pattern(children:list[Element])->bool:
 if not children or any(strip_ns(child.tag)!="line" for child in children):
  return False
 return all(abs(parse_length(child.get("x1"))-parse_length(child.get("x2")))<=_EPSILON or abs(parse_length(child.get("y1"))-parse_length(child.get("y2")))<=_EPSILON for child in children)
def _is_simple_dot_pattern(children:list[Element])->bool:
 return bool(children)and all(strip_ns(child.tag)=="circle" for child in children)
def _pattern_child_css(base_css:dict[str,str],child:Element)->dict[str,str]:
 css=dict(base_css)
 css.update(parse_style_attr(child.get("style","")))
 for name in("fill","stroke","stroke-width","fill-opacity","stroke-opacity","opacity","stroke-linecap","stroke-linejoin","stroke-dasharray",):
  value=child.get(name)
  if value is not None:
   css[name]=value
 css.pop("filter",None)
 return css
def _translated_line(child:Element,offset_x:float,offset_y:float)->Element:
 return Element("line",{**child.attrib,"x1":f"{offset_x+parse_length(child.get('x1')):.6f}","y1":f"{offset_y+parse_length(child.get('y1')):.6f}","x2":f"{offset_x+parse_length(child.get('x2')):.6f}","y2":f"{offset_y+parse_length(child.get('y2')):.6f}",},)
def _translated_circle(child:Element,offset_x:float,offset_y:float)->Element:
 return Element("circle",{**child.attrib,"cx":f"{offset_x+parse_length(child.get('cx')):.6f}","cy":f"{offset_y+parse_length(child.get('cy')):.6f}",},)
def _line_outside_rect(line:Element,x:float,y:float,width:float,height:float,)->bool:
 x1=parse_length(line.get("x1"))
 y1=parse_length(line.get("y1"))
 x2=parse_length(line.get("x2"))
 y2=parse_length(line.get("y2"))
 if x1<x-_EPSILON or x2<x-_EPSILON or x1>x+width+_EPSILON or x2>x+width+_EPSILON:
  return True
 if y1<y-_EPSILON or y2<y-_EPSILON or y1>y+height+_EPSILON or y2>y+height+_EPSILON:
  return True
 return False
def _circle_outside_rect(circle:Element,x:float,y:float,width:float,height:float,)->bool:
 cx=parse_length(circle.get("cx"))
 cy=parse_length(circle.get("cy"))
 radius=parse_length(circle.get("r"))
 return(cx+radius<x-_EPSILON or cy+radius<y-_EPSILON or cx-radius>x+width+_EPSILON or cy-radius>y+height+_EPSILON)
def _child_attr(child:Element,name:str,*,default:str)->str:
 styles=parse_style_attr(child.get("style",""))
 return child.get(name)or styles.get(name)or default
"""Helpers for embedding unsupported SVG fragments as self-contained SVG images."""
import re
from copy import deepcopy
from urllib.parse import quote_from_bytes
from xml.etree.ElementTree import Element,tostring
_LOCAL_REF_RE=re.compile(r"url\(#([^)]+)\)|(?:^|[\s:,])#([A-Za-z][\w.-]*)")
_SVG_NS="http://www.w3.org/2000/svg"
_XLINK_NS="http://www.w3.org/1999/xlink"
def data_uri_from_svg(svg_text:str)->str:
 return f"data:image/svg+xml,{quote_from_bytes(svg_text.encode('utf-8'),safe='')}"
def matrix_to_svg(matrix:list[float])->str:
 return "matrix({})".format(" ".join(f"{value:.6f}" for value in matrix))
def _style_text(computed_css:dict[str,str]|None)->str|None:
 if not computed_css:
  return None
 items=[f"{name}:{value}" for name,value in sorted(computed_css.items())if value]
 return ";".join(items)if items else None
def _collect_refs_from_text(text:str,seen:set[str])->None:
 for m in _LOCAL_REF_RE.finditer(text):
  ref=m.group(1)or m.group(2)
  if ref:
   seen.add(ref)
def _collect_refs(node:Element,seen:set[str])->None:
 for val in node.attrib.values():
  _collect_refs_from_text(val,seen)
 for child in node:
  _collect_refs(child,seen)
def _slice_defs(svg_root:Element,elem:Element)->Element|None:
 id_to_def:dict[str,Element]={}
 for child in svg_root:
  if strip_ns(child.tag)=="defs":
   for def_child in child:
    def_id=def_child.get("id")
    if def_id:
     id_to_def[def_id]=def_child
 if not id_to_def:
  return None
 needed:set[str]=set()
 _collect_refs(elem,needed)
 for child in svg_root:
  if strip_ns(child.tag)=="style":
   _collect_refs_from_text(child.text or "",needed)
 frontier=set(needed)
 while frontier:
  next_frontier:set[str]=set()
  for ref_id in frontier:
   def_elem=id_to_def.get(ref_id)
   if def_elem is not None:
    inner:set[str]=set()
    _collect_refs(def_elem,inner)
    new=inner-needed
    needed|=new
    next_frontier|=new
  frontier=next_frontier
 relevant=[deepcopy(id_to_def[ref_id])for ref_id in needed if ref_id in id_to_def]
 if not relevant:
  return None
 defs_elem=Element("defs")
 for child in relevant:
  defs_elem.append(child)
 return defs_elem
def build_fallback_svg_data_uri(svg_root:Element,elem:Element,*,parent_matrix:list[float],bbox:tuple[float,float,float,float],computed_css:dict[str,str]|None,padding:float=0.0,report:ConversionReport|None=None,)->str:
 x,y,width,height=bbox
 x-=padding
 y-=padding
 width+=2.0*padding
 height+=2.0*padding
 width=max(width,1.0)
 height=max(height,1.0)
 wrapper=Element("svg",{"xmlns":_SVG_NS,"xmlns:xlink":_XLINK_NS,"viewBox":f"{x:.6f} {y:.6f} {width:.6f} {height:.6f}","width":f"{width:.6f}","height":f"{height:.6f}",},)
 sliced=_slice_defs(svg_root,elem)
 if sliced is not None:
  wrapper.append(sliced)
 for child in svg_root:
  if strip_ns(child.tag)=="style":
   wrapper.append(deepcopy(child))
 outer_group=Element("g",{"transform":matrix_to_svg(parent_matrix)})
 fragment=deepcopy(elem)
 merged_style=parse_style_attr(fragment.get("style",""))
 merged_style.update(dict(computed_css or{}))
 style_text=_style_text(merged_style)
 if style_text:
  fragment.set("style",style_text)
 outer_group.append(fragment)
 wrapper.append(outer_group)
 svg_text=tostring(wrapper,encoding="unicode")
 if report is not None:
  report.add_asset(href=f"inline:{strip_ns(elem.tag)}",status="embedded-svg-fallback",mime_type="image/svg+xml",message=f"Embedded fallback generated for <{strip_ns(elem.tag)}>.",)
 return data_uri_from_svg(svg_text)
"""Path-building helpers for primitive SVG shapes."""
def ellipse_path_d(cx:float,cy:float,rx:float,ry:float)->str:
 return(f"M {cx-rx:.6f} {cy:.6f} "f"A {rx:.6f} {ry:.6f} 0 1 0 {cx+rx:.6f} {cy:.6f} "f"A {rx:.6f} {ry:.6f} 0 1 0 {cx-rx:.6f} {cy:.6f} Z")
def rect_path_d(x:float,y:float,width:float,height:float)->str:
 return f"M {x:.6f} {y:.6f} H {x+width:.6f} V {y+height:.6f} H {x:.6f} Z"
def rounded_rect_path_d(x:float,y:float,width:float,height:float,rx:float,ry:float)->str:
 rx=max(0.0,min(rx,width/2))
 ry=max(0.0,min(ry,height/2))
 if rx==0 or ry==0:
  return rect_path_d(x,y,width,height)
 return(f"M {x+rx:.6f} {y:.6f} "f"H {x+width-rx:.6f} "f"A {rx:.6f} {ry:.6f} 0 0 1 {x+width:.6f} {y+ry:.6f} "f"V {y+height-ry:.6f} "f"A {rx:.6f} {ry:.6f} 0 0 1 {x+width-rx:.6f} {y+height:.6f} "f"H {x+rx:.6f} "f"A {rx:.6f} {ry:.6f} 0 0 1 {x:.6f} {y+height-ry:.6f} "f"V {y+ry:.6f} "f"A {rx:.6f} {ry:.6f} 0 0 1 {x+rx:.6f} {y:.6f} Z")
"""Shared style and marker helpers for SVG element emitters."""
import math
from collections.abc import Sequence
from xml.etree.ElementTree import Element
def add_metadata_styles(builder:StyleBuilder,elem:Element,ctx:EmitterContext)->StyleBuilder:
 return builder.add("tooltip",tooltip_value(elem)).add("link",link_value(ctx.link_url))
def add_gradient_styles(builder:StyleBuilder,gradient:GradientStyle|None)->StyleBuilder:
 return builder.extend_pairs(gradient_entries(gradient))
def add_filter_styles(builder:StyleBuilder,ctx:EmitterContext,elem:Element,filter_ref:str|None,*,fallback_color:str|None=None,)->StyleBuilder:
 resolution=ctx.defs.resolve_filter_style(filter_ref,fallback_color=fallback_color)
 if resolution is None:
  return builder
 if resolution.approximated:
  ctx.report.add_issue(FILTER_SIMPLIFIED_NATIVE,"warning",resolution.detail,element_tag=elem.tag.rsplit("}",1)[-1],element_id=elem.get("id"),)
 ctx.report.record_feature_observation(note_filter_usage(native=not resolution.approximated,approximated=resolution.approximated,detail=resolution.detail,))
 return builder.extend_pairs(resolution.entries)
def emit_midpoint_markers(ctx:EmitterContext,points:Sequence[Point2D],color:str,opacity:int,size:float=8.0,)->None:
 for px,py in points:
  style=(StyleBuilder().add_flag("ellipse").add("fillColor",color).add("strokeColor",color).add("opacity",opacity).build())
  ctx.add(make_bounds_vertex(ctx,style,px-size/2,py-size/2,size,size))
def segment_angle_degrees(start:Point2D,end:Point2D)->float:
 return math.degrees(math.atan2(end[1]-start[1],end[0]-start[0]))
def extend_edge_endpoints(source:Point2D,target:Point2D,waypoints:Sequence[Point2D],*,start_extension:float=0.0,end_extension:float=0.0,)->tuple[Point2D,Point2D]:
 def extend(point:Point2D,neighbor:Point2D,distance:float)->Point2D:
  dx=point[0]-neighbor[0]
  dy=point[1]-neighbor[1]
  length=math.hypot(dx,dy)
  if distance<=0.0 or length<=1e-9:
   return point
  return point[0]+dx/length*distance,point[1]+dy/length*distance
 source_neighbor=waypoints[0]if waypoints else target
 target_neighbor=waypoints[-1]if waypoints else source
 return(extend(source,source_neighbor,start_extension),extend(target,target_neighbor,end_extension),)
def add_native_arrow_styles(style:StyleBuilder,start_arrow:str,end_arrow:str,)->StyleBuilder:
 style.add("startArrow",start_arrow).add("endArrow",end_arrow)
 style.add("startFill",0 if start_arrow=="open" else 1,when=start_arrow!="none")
 style.add("endFill",0 if end_arrow=="open" else 1,when=end_arrow!="none")
 return style
def emit_endpoint_marker(ctx:EmitterContext,point:Point2D,shape:str,color:str,opacity:int,*,size:float=10.0,rotation:float|None=None,)->None:
 style=StyleBuilder()
 if shape=="ellipse":
  style.add_flag("ellipse")
 elif shape=="diamond":
  style.add("shape","rhombus").add("whiteSpace","wrap").add("html",1)
 elif shape=="triangle":
  style.add("shape","triangle").add("whiteSpace","wrap").add("html",1)
 else:
  style.add("rounded",0).add("whiteSpace","wrap").add("html",1)
 style.add("fillColor",color).add("strokeColor",color).add("opacity",opacity)
 style.add("rotation",f"{rotation:.2f}",when=rotation is not None)
 ctx.add(make_bounds_vertex(ctx,style.build(),point[0]-size/2,point[1]-size/2,size,size))
"""Shared helpers for primitive shape emitters."""
from collections.abc import Sequence
from xml.etree.ElementTree import Element
def multi_stop_filter_refs(ctx:EmitterContext,filter_ref:str|None)->tuple[str|None,str|None]:
 normalized=normalize_filter_ref(filter_ref)
 if normalized is None:
  return None,None
 if ctx.rendering_options.filter_policy!="prefer-native":
  return normalized,None
 return None,normalized if ctx.defs.supports_filter(normalized)else None
def emit_polygon_stencil(ctx:EmitterContext,elem:Element,corners:Sequence[Point2D],fill:str|None,gradient:GradientStyle|None,stroke:str|None,stroke_width:float,opacity:int,fill_opacity:int,stroke_opacity:int,dash:str="",filter_ref:str|None=None,)->None:
 box=bounds_from_points(corners)
 def norm_x(x:float)->float:
  return(x-box.x)/box.width*100
 def norm_y(y:float)->float:
  return(y-box.y)/box.height*100
 path_xml=f'<move x="{norm_x(corners[0][0]):.2f}" y="{norm_y(corners[0][1]):.2f}"/>'
 for corner_x,corner_y in corners[1:]:
  path_xml+=f'<line x="{norm_x(corner_x):.2f}" y="{norm_y(corner_y):.2f}"/>'
 path_xml+="<close/>"
 xml=('<shape w="100" h="100" aspect="variable" strokewidth="inherit">' f"<background><path>{path_xml}</path><fillstroke/></background></shape>")
 stencil_style=make_stencil_style_from_xml(xml,fill,stroke,stroke_width,opacity)
 if not stencil_style:
  return
 style=StyleBuilder().extend_raw(stencil_style)
 style.add("fillOpacity",fill_opacity).add("strokeOpacity",stroke_opacity)
 add_gradient_styles(style,gradient)
 style.extend_raw(dash)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,filter_ref,fallback_color=fill if fill!="none" else stroke)
 ctx.add(make_box_vertex(ctx,style.build(),box))
def emit_stencil_commands(ctx:EmitterContext,elem:Element,commands:Sequence[PathCommand],fill:str|None,gradient:GradientStyle|None,stroke:str|None,stroke_width:float,opacity:int,fill_opacity:int,stroke_opacity:int,dash:str="",fill_rule:str="nonzero",linecap:str="flat",linejoin:str="miter",filter_ref:str|None=None,)->None:
 bbox=commands_bbox(commands)
 if not bbox:
  return
 bx,by,bw,bh=bbox
 stencil_style=make_stencil_style_from_commands(commands,bx,by,bw,bh,fill,stroke,stroke_width,opacity,fill_rule=fill_rule,linecap=linecap,linejoin=linejoin,)
 if not stencil_style:
  return
 style=StyleBuilder().extend_raw(stencil_style)
 style.add("fillOpacity",fill_opacity).add("strokeOpacity",stroke_opacity)
 add_gradient_styles(style,gradient)
 style.extend_raw(dash)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,filter_ref,fallback_color=fill if fill!="none" else stroke)
 box=bounds_from_points(((bx,by),(bx+bw,by+bh)))
 ctx.add(make_box_vertex(ctx,style.build(),box))
def emit_transformed_path_stencil(ctx:EmitterContext,elem:Element,path_data:str,matrix:Matrix,fill:str|None,gradient:GradientStyle|None,stroke:str|None,stroke_width:float,opacity:int,fill_opacity:int,stroke_opacity:int,dash:str="",linecap:str="flat",linejoin:str="miter",filter_ref:str|None=None,)->None:
 commands=path_commands(path_data,point_transform=lambda x,y:apply_pt(matrix,x,y))
 emit_stencil_commands(ctx,elem,commands,fill,gradient,stroke,stroke_width,opacity,fill_opacity,stroke_opacity,dash,linecap=linecap,linejoin=linejoin,filter_ref=filter_ref,)
"""Native approximations for SVG multi-stop gradients on simple shapes."""
import math
from dataclasses import dataclass,replace
from xml.etree.ElementTree import Element
_APPROXIMATION_TAGS:frozenset[str]=frozenset({"rect","circle","ellipse"})
_LINEAR_PATH_TAGS:frozenset[str]=frozenset({"path"})
_RADIAL_MAX_STEPS=96
_RADIAL_MIN_STEPS=16
_RADIAL_TARGET_PX=1.5
_SAMPLES_PER_CURVE=32
_BAND_OVERLAP=0.002
@dataclass(frozen=True)
class ShapeSpec:
 kind:str
 x:float
 y:float
 width:float
 height:float
 rx:float=0.0
 ry:float=0.0
 @property
 def cx(self)->float:
  return self.x+self.width/2
 @property
 def cy(self)->float:
  return self.y+self.height/2
 def scaled(self,ratio:float)->ShapeSpec:
  ratio=max(0.0,min(1.0,ratio))
  new_width=self.width*ratio
  new_height=self.height*ratio
  return ShapeSpec(kind=self.kind,x=self.cx-new_width/2,y=self.cy-new_height/2,width=new_width,height=new_height,rx=self.rx*ratio,ry=self.ry*ratio,)
def is_multi_stop_gradient(gradient:GradientStyle|None)->bool:
 return gradient is not None and len(gradient["stops"])>2
def supports_multi_stop_gradient_approximation(tag:str,matrix:Matrix,gradient:GradientStyle|None,*,filter_ref:str|None=None,)->bool:
 if gradient is None or not is_multi_stop_gradient(gradient)or filter_ref:
  return False
 if has_shear(matrix):
  return False
 if tag in _APPROXIMATION_TAGS:
  return True
 if tag in _LINEAR_PATH_TAGS and gradient["kind"]=="linear":
  return True
 return False
def emit_multi_stop_gradient_approximation(ctx:EmitterContext,elem:Element,matrix:Matrix,spec:ShapeSpec,gradient:GradientStyle,*,stroke:str,stroke_width:float,opacity:int,fill_opacity:int,stroke_opacity:int,dash:str="",filter_ref:str|None=None,)->None:
 group_id=ctx.next_id()
 child_cells:list[Cell]=[]
 child_ctx=replace(ctx.with_parent(group_id),add_cell=child_cells.append)
 if gradient["kind"]=="radial":
  _emit_radial_layers(child_ctx,elem,matrix,spec,gradient,opacity=opacity,fill_opacity=fill_opacity,filter_ref=filter_ref,)
 else:
  _emit_linear_bands(child_ctx,elem,matrix,spec,gradient,opacity=opacity,fill_opacity=fill_opacity,filter_ref=filter_ref,)
 if stroke!="none" and stroke_width>0:
  _emit_shape_stroke_overlay(child_ctx,elem,matrix,spec,stroke=stroke,stroke_width=stroke_width,opacity=opacity,stroke_opacity=stroke_opacity,dash=dash,filter_ref=filter_ref,)
 if not child_cells:
  return
 direct_children=[cell for cell in child_cells if cell.parent==group_id]
 gx,gy,gw,gh=group_bbox(direct_children)
 ctx.add(make_bounds_vertex(ctx,"group;",gx,gy,gw,gh,cell_id=group_id))
 shift_cells(direct_children,gx,gy)
 for cell in child_cells:
  ctx.add(cell)
def _emit_linear_bands(ctx:EmitterContext,elem:Element,matrix:Matrix,spec:ShapeSpec,gradient:GradientStyle,*,opacity:int,fill_opacity:int,filter_ref:str|None=None,)->None:
 stops=gradient["stops"]
 direction=gradient["direction"]
 vertical=direction in{"east","west"}
 reverse=direction in{"west","north"}
 band_dir="east" if vertical else "south"
 for i in range(len(stops)-1):
  left_stop=stops[i]
  right_stop=stops[i+1]
  if reverse:
   phys_start=1.0-right_stop["offset"]
   phys_end=1.0-left_stop["offset"]
   fill_color=right_stop["color"]
   grad_color=left_stop["color"]
  else:
   phys_start=left_stop["offset"]
   phys_end=right_stop["offset"]
   fill_color=left_stop["color"]
   grad_color=right_stop["color"]
  if phys_end-phys_start<=1e-6:
   continue
  phys_end_draw=min(phys_end+_BAND_OVERLAP,1.0)if i<len(stops)-2 else phys_end
  polygon=_band_polygon(spec,phys_start,phys_end_draw,vertical=vertical)
  if len(polygon)<3:
   continue
  transformed=[apply_pt(matrix,px,py)for px,py in polygon]
  band_gradient:GradientStyle={"color":fill_color,"color2":grad_color,"direction":band_dir,"kind":"linear","stops":[{"offset":0.0,"color":fill_color},{"offset":1.0,"color":grad_color},],}
  emit_polygon_stencil(ctx,elem,transformed,fill_color,band_gradient,"none",1.0,opacity,fill_opacity,100,filter_ref=filter_ref,)
def _emit_radial_layers(ctx:EmitterContext,elem:Element,matrix:Matrix,spec:ShapeSpec,gradient:GradientStyle,*,opacity:int,fill_opacity:int,filter_ref:str|None=None,)->None:
 steps=_radial_step_count(gradient,spec)
 for band_index in range(steps,0,-1):
  ratio=band_index/steps
  offset=(band_index-0.5)/steps
  color=_color_at_offset(gradient,offset)
  _emit_filled_shape(ctx,elem,matrix,spec.scaled(ratio),color=color,opacity=opacity,fill_opacity=fill_opacity,filter_ref=filter_ref,)
def _emit_filled_shape(ctx:EmitterContext,elem:Element,matrix:Matrix,spec:ShapeSpec,*,color:str,opacity:int,fill_opacity:int,filter_ref:str|None=None,)->None:
 if spec.width<=0 or spec.height<=0:
  return
 style=StyleBuilder()
 if spec.kind=="ellipse":
  box=ellipse_bounds(matrix,spec.cx,spec.cy,spec.width/2,spec.height/2)
  rotation=box.rotation_if_visible()
  style.add_flag("ellipse")
 else:
  box=rect_bounds(matrix,spec.x,spec.y,spec.width,spec.height)
  rotation=box.rotation_if_visible()
  if spec.rx>0 or spec.ry>0:
   shorter_side=min(spec.width,spec.height)if spec.width>0 and spec.height>0 else 1.0
   arc_pct=min(50,round(max(spec.rx,spec.ry)/shorter_side*100))
   style.add("rounded",1).add("arcSize",arc_pct)
  else:
   style.add("rounded",0)
 rotation_style=f"{rotation:.2f}" if rotation is not None else None
 style.add("whiteSpace","wrap").add("html",1)
 style.add("fillColor",color).add("strokeColor","none").add("strokeWidth",1.0)
 style.add("opacity",opacity).add("fillOpacity",fill_opacity).add("strokeOpacity",100)
 style.add("rotation",rotation_style,when=rotation_style is not None)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,filter_ref,fallback_color=color)
 ctx.add(make_box_vertex(ctx,style.build(),box))
def _emit_shape_stroke_overlay(ctx:EmitterContext,elem:Element,matrix:Matrix,spec:ShapeSpec,*,stroke:str,stroke_width:float,opacity:int,stroke_opacity:int,dash:str,filter_ref:str|None=None,)->None:
 style=StyleBuilder()
 if spec.kind=="ellipse":
  box=ellipse_bounds(matrix,spec.cx,spec.cy,spec.width/2,spec.height/2)
  rotation=box.rotation_if_visible()
  style.add_flag("ellipse")
 else:
  box=rect_bounds(matrix,spec.x,spec.y,spec.width,spec.height)
  rotation=box.rotation_if_visible()
  if spec.rx>0 or spec.ry>0:
   shorter_side=min(spec.width,spec.height)if spec.width>0 and spec.height>0 else 1.0
   arc_pct=min(50,round(max(spec.rx,spec.ry)/shorter_side*100))
   style.add("rounded",1).add("arcSize",arc_pct)
  else:
   style.add("rounded",0)
 rotation_style=f"{rotation:.2f}" if rotation is not None else None
 style.add("whiteSpace","wrap").add("html",1)
 style.add("fillColor","none").add("strokeColor",stroke).add("strokeWidth",stroke_width)
 style.add("opacity",opacity).add("fillOpacity",100).add("strokeOpacity",stroke_opacity)
 style.extend_raw(dash)
 style.add("rotation",rotation_style,when=rotation_style is not None)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,filter_ref,fallback_color=stroke)
 ctx.add(make_box_vertex(ctx,style.build(),box))
def _radial_step_count(gradient:GradientStyle,spec:ShapeSpec)->int:
 radius=max(spec.width,spec.height)/2
 steps_from_size=max(1,round(radius/_RADIAL_TARGET_PX))
 steps_from_stops=(len(gradient["stops"])-1)*4
 return min(_RADIAL_MAX_STEPS,max(_RADIAL_MIN_STEPS,steps_from_size,steps_from_stops))
def _band_polygon(spec:ShapeSpec,start:float,end:float,*,vertical:bool)->list[Point2D]:
 if vertical:
  x0=spec.x+spec.width*start
  x1=spec.x+spec.width*end
  return _vertical_band_polygon(spec,x0,x1)
 y0=spec.y+spec.height*start
 y1=spec.y+spec.height*end
 return _horizontal_band_polygon(spec,y0,y1)
def _vertical_band_polygon(spec:ShapeSpec,x0:float,x1:float)->list[Point2D]:
 x0=max(spec.x,min(spec.x+spec.width,x0))
 x1=max(spec.x,min(spec.x+spec.width,x1))
 if x1-x0<=1e-6:
  return[]
 xs=_sample_axis(x0,x1)
 top=[(px,_shape_top(spec,px))for px in xs]
 bottom=[(px,_shape_bottom(spec,px))for px in reversed(xs)]
 return top+bottom
def _horizontal_band_polygon(spec:ShapeSpec,y0:float,y1:float)->list[Point2D]:
 y0=max(spec.y,min(spec.y+spec.height,y0))
 y1=max(spec.y,min(spec.y+spec.height,y1))
 if y1-y0<=1e-6:
  return[]
 ys=_sample_axis(y0,y1)
 left=[(_shape_left(spec,py),py)for py in ys]
 right=[(_shape_right(spec,py),py)for py in reversed(ys)]
 return left+right
def _sample_axis(start:float,end:float)->list[float]:
 return[start+(end-start)*index/(_SAMPLES_PER_CURVE-1)for index in range(_SAMPLES_PER_CURVE)]
def _shape_top(spec:ShapeSpec,px:float)->float:
 if spec.kind=="ellipse":
  return spec.cy-(spec.height/2)*_ellipse_term(spec,px)
 if spec.rx<=0 or spec.ry<=0:
  return spec.y
 left_limit=spec.x+spec.rx
 right_limit=spec.x+spec.width-spec.rx
 if px<left_limit:
  return _rounded_corner_y(spec.x+spec.rx,spec.y+spec.ry,spec.rx,spec.ry,px,upper=True)
 if px>right_limit:
  return _rounded_corner_y(spec.x+spec.width-spec.rx,spec.y+spec.ry,spec.rx,spec.ry,px,upper=True)
 return spec.y
def _shape_bottom(spec:ShapeSpec,px:float)->float:
 if spec.kind=="ellipse":
  return spec.cy+(spec.height/2)*_ellipse_term(spec,px)
 if spec.rx<=0 or spec.ry<=0:
  return spec.y+spec.height
 left_limit=spec.x+spec.rx
 right_limit=spec.x+spec.width-spec.rx
 if px<left_limit:
  return _rounded_corner_y(spec.x+spec.rx,spec.y+spec.height-spec.ry,spec.rx,spec.ry,px,upper=False,)
 if px>right_limit:
  return _rounded_corner_y(spec.x+spec.width-spec.rx,spec.y+spec.height-spec.ry,spec.rx,spec.ry,px,upper=False,)
 return spec.y+spec.height
def _shape_left(spec:ShapeSpec,py:float)->float:
 if spec.kind=="ellipse":
  return spec.cx-(spec.width/2)*_ellipse_term(spec,py,horizontal=False)
 if spec.rx<=0 or spec.ry<=0:
  return spec.x
 top_limit=spec.y+spec.ry
 bottom_limit=spec.y+spec.height-spec.ry
 if py<top_limit:
  return _rounded_corner_x(spec.x+spec.rx,spec.y+spec.ry,spec.rx,spec.ry,py,left_side=True)
 if py>bottom_limit:
  return _rounded_corner_x(spec.x+spec.rx,spec.y+spec.height-spec.ry,spec.rx,spec.ry,py,left_side=True,)
 return spec.x
def _shape_right(spec:ShapeSpec,py:float)->float:
 if spec.kind=="ellipse":
  return spec.cx+(spec.width/2)*_ellipse_term(spec,py,horizontal=False)
 if spec.rx<=0 or spec.ry<=0:
  return spec.x+spec.width
 top_limit=spec.y+spec.ry
 bottom_limit=spec.y+spec.height-spec.ry
 if py<top_limit:
  return _rounded_corner_x(spec.x+spec.width-spec.rx,spec.y+spec.ry,spec.rx,spec.ry,py,left_side=False,)
 if py>bottom_limit:
  return _rounded_corner_x(spec.x+spec.width-spec.rx,spec.y+spec.height-spec.ry,spec.rx,spec.ry,py,left_side=False,)
 return spec.x+spec.width
def _ellipse_term(spec:ShapeSpec,axis_value:float,*,horizontal:bool=True)->float:
 radius=spec.width/2 if horizontal else spec.height/2
 center=spec.cx if horizontal else spec.cy
 if radius<=0:
  return 0.0
 normalized=(axis_value-center)/radius
 return math.sqrt(max(0.0,1.0-normalized*normalized))
def _rounded_corner_y(cx:float,cy:float,rx:float,ry:float,px:float,*,upper:bool)->float:
 if rx<=0 or ry<=0:
  return cy
 normalized=max(-1.0,min(1.0,(px-cx)/rx))
 delta=ry*math.sqrt(max(0.0,1.0-normalized*normalized))
 return cy-delta if upper else cy+delta
def _rounded_corner_x(cx:float,cy:float,rx:float,ry:float,py:float,*,left_side:bool)->float:
 if rx<=0 or ry<=0:
  return cx
 normalized=max(-1.0,min(1.0,(py-cy)/ry))
 delta=rx*math.sqrt(max(0.0,1.0-normalized*normalized))
 return cx-delta if left_side else cx+delta
def _color_at_offset(gradient:GradientStyle,offset:float)->str:
 stops=gradient["stops"]
 if not stops:
  return gradient["color"]
 if offset<=stops[0]["offset"]:
  return stops[0]["color"]
 if offset>=stops[-1]["offset"]:
  return stops[-1]["color"]
 for left,right in zip(stops,stops[1:]):
  if left["offset"]<=offset<=right["offset"]:
   span=right["offset"]-left["offset"]
   blend=0.0 if span<=1e-9 else(offset-left["offset"])/span
   return _blend_hex(left["color"],right["color"],blend)
 return stops[-1]["color"]
def _blend_hex(left:str,right:str,blend:float)->str:
 blend=max(0.0,min(1.0,blend))
 left_rgb=_hex_to_rgb(left)
 right_rgb=_hex_to_rgb(right)
 mixed=tuple(round(lv+(rv-lv)*blend)for lv,rv in zip(left_rgb,right_rgb))
 return "#{:02x}{:02x}{:02x}".format(*mixed)
def _hex_to_rgb(color:str)->tuple[int,int,int]:
 text=color.strip()
 if len(text)==7 and text.startswith("#"):
  return int(text[1:3],16),int(text[3:5],16),int(text[5:7],16)
 return 0,0,0
def emit_path_multi_stop_gradient_approximation(ctx:EmitterContext,elem:Element,commands:list,bbox:tuple[float,float,float,float],gradient:GradientStyle,*,stroke:str,stroke_width:float,opacity:int,fill_opacity:int,stroke_opacity:int,fill_rule:str="nonzero",linecap:str="flat",linejoin:str="miter",dash:str="",filter_ref:str|None=None,)->None:
 polygon=commands_to_polygon(list(commands))
 if len(polygon)<3:
  return
 bx,by,bw,bh=bbox
 group_id=ctx.next_id()
 child_cells:list[Cell]=[]
 child_ctx=replace(ctx.with_parent(group_id),add_cell=child_cells.append)
 stops=gradient["stops"]
 direction=gradient["direction"]
 vertical=direction in{"east","west"}
 reverse=direction in{"west","north"}
 band_dir="east" if vertical else "south"
 n=len(stops)-1
 for i in range(n):
  left_stop=stops[i]
  right_stop=stops[i+1]
  if reverse:
   phys_start=1.0-right_stop["offset"]
   phys_end=1.0-left_stop["offset"]
   fill_color=right_stop["color"]
   grad_color=left_stop["color"]
  else:
   phys_start=left_stop["offset"]
   phys_end=right_stop["offset"]
   fill_color=left_stop["color"]
   grad_color=right_stop["color"]
  if phys_end-phys_start<=1e-6:
   continue
  phys_end_draw=min(phys_end+_BAND_OVERLAP,1.0)if i<n-1 else phys_end
  if vertical:
   lo=bx+bw*phys_start
   hi=bx+bw*phys_end_draw
  else:
   lo=by+bh*phys_start
   hi=by+bh*phys_end_draw
  clipped=clip_polygon_strip(polygon,lo,hi,vertical=vertical)
  if len(clipped)<3:
   continue
  band_gradient:GradientStyle={"color":fill_color,"color2":grad_color,"direction":band_dir,"kind":"linear","stops":[{"offset":0.0,"color":fill_color},{"offset":1.0,"color":grad_color},],}
  emit_polygon_stencil(child_ctx,elem,clipped,fill_color,band_gradient,"none",1.0,opacity,fill_opacity,100,filter_ref=filter_ref,)
 if stroke!="none" and stroke_width>0:
  emit_stencil_commands(child_ctx,elem,commands,"none",None,stroke,stroke_width,opacity,100,stroke_opacity,dash,fill_rule,linecap,linejoin,filter_ref=filter_ref,)
 if not child_cells:
  return
 direct_children=[cell for cell in child_cells if cell.parent==group_id]
 gx,gy,gw,gh=group_bbox(direct_children)
 ctx.add(make_bounds_vertex(ctx,"group;",gx,gy,gw,gh,cell_id=group_id))
 shift_cells(direct_children,gx,gy)
 for cell in child_cells:
  ctx.add(cell)
"""Emitters for SVG primitive shapes."""
from xml.etree.ElementTree import Element
def emit_line(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str]|None=None)->None:
 visual=get_visual(elem,css)
 (x1,y1),(x2,y2)=line_endpoints(matrix,parse_length(elem.get("x1")),parse_length(elem.get("y1")),parse_length(elem.get("x2")),parse_length(elem.get("y2")),)
 stroke_color=visual["stroke"]or "#000000"
 opacity=opacity_pct(visual["opacity"])
 stroke_opacity=opacity_pct(visual["stroke_opacity"])
 geometry_scale=stroke_scale(matrix)
 stroke_width=visual["stroke_width"]*geometry_scale
 start_arrow=ctx.defs.resolve_marker(visual["marker_start"])
 end_arrow=ctx.defs.resolve_marker(visual["marker_end"])
 start_shape=ctx.defs.resolve_custom_marker_shape(visual["marker_start"])
 end_shape=ctx.defs.resolve_custom_marker_shape(visual["marker_end"])
 if start_shape is not None:
  start_arrow="none"
 if end_shape is not None:
  end_arrow="none"
 linecap=visual["linecap"]
 if start_arrow!="none" or end_arrow!="none" or start_shape is not None or end_shape is not None:
  ctx.report.record_feature_observation(note_marker_usage())
 if linecap!="flat" and start_arrow=="none" and end_arrow=="none":
  pad=stroke_width/2
  bx=min(x1,x2)-pad
  by=min(y1,y2)-pad
  bw=max(abs(x2-x1)+stroke_width,1.0)
  bh=max(abs(y2-y1)+stroke_width,1.0)
  cmds=[("move",((x1,y1),)),("line",((x2,y2),))]
  stencil_style=make_stencil_style_from_commands(cmds,bx,by,bw,bh,"none",stroke_color,stroke_width,opacity,linecap=linecap)
  if stencil_style:
   ctx.report.record_feature_observation(note_shape_usage(approximated=True))
   style=StyleBuilder().extend_raw(stencil_style)
   style.add("strokeOpacity",stroke_opacity)
   style.extend_raw(visual["dash_style"])
   add_metadata_styles(style,elem,ctx)
   add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=stroke_color)
   ctx.add(make_bounds_vertex(ctx,style.build(),bx,by,bw,bh))
  return
 ctx.report.record_feature_observation(note_shape_usage(approximated=False))
 (x1,y1),(x2,y2)=extend_edge_endpoints((x1,y1),(x2,y2),(),start_extension=(ctx.defs.resolve_marker_extension(visual["marker_start"],at_start=True,stroke_width=stroke_width,user_scale=geometry_scale,)if start_shape is None else 0.0),end_extension=(ctx.defs.resolve_marker_extension(visual["marker_end"],at_start=False,stroke_width=stroke_width,user_scale=geometry_scale,)if end_shape is None else 0.0),)
 style=StyleBuilder()
 style.add("rounded",0)
 add_native_arrow_styles(style,start_arrow,end_arrow).add("html",1)
 style.add("strokeColor",stroke_color).add("strokeWidth",f"{stroke_width:.2f}")
 style.add("opacity",opacity).add("strokeOpacity",stroke_opacity)
 style.extend_raw(visual["dash_style"])
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=stroke_color)
 ctx.add(make_edge(ctx,style.build(),(x1,y1),(x2,y2)))
 marker_size=max(stroke_width*4.0,8.0)
 if start_shape is not None:
  emit_endpoint_marker(ctx,(x1,y1),start_shape,stroke_color,opacity,size=marker_size,rotation=segment_angle_degrees((x1,y1),(x2,y2))+180.0,)
 if end_shape is not None:
  emit_endpoint_marker(ctx,(x2,y2),end_shape,stroke_color,opacity,size=marker_size,rotation=segment_angle_degrees((x1,y1),(x2,y2)),)
def emit_circle(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str]|None=None)->None:
 visual=get_visual(elem,css)
 cx0=parse_length(elem.get("cx"))
 cy0=parse_length(elem.get("cy"))
 radius=parse_length(elem.get("r"))
 fill,gradient=ctx.defs.resolve_fill(visual["fill"]or "none")
 stroke=visual["stroke"]or "none"
 opacity=opacity_pct(visual["opacity"])
 fill_opacity=opacity_pct(visual["fill_opacity"])
 stroke_opacity=opacity_pct(visual["stroke_opacity"])
 stroke_width=visual["stroke_width"]*stroke_scale(matrix)
 approx_support_filter,approx_style_filter=multi_stop_filter_refs(ctx,visual["filter"])
 if supports_multi_stop_gradient_approximation("circle",matrix,gradient,filter_ref=approx_support_filter):
  assert gradient is not None
  ctx.report.record_feature_observation(note_shape_usage(approximated=False))
  ctx.report.record_feature_observation(note_gradient_usage(approximated=True))
  emit_multi_stop_gradient_approximation(ctx,elem,matrix,ShapeSpec("ellipse",cx0-radius,cy0-radius,radius*2,radius*2),gradient,stroke=stroke,stroke_width=stroke_width,opacity=opacity,fill_opacity=fill_opacity,stroke_opacity=stroke_opacity,dash=visual["dash_style"],filter_ref=approx_style_filter,)
  return
 if has_shear(matrix):
  ctx.report.record_feature_observation(note_shape_usage(approximated=True))
  if gradient is not None:
   ctx.report.record_feature_observation(note_gradient_usage(approximated=False))
  emit_transformed_path_stencil(ctx,elem,ellipse_path_d(cx0,cy0,radius,radius),matrix,fill,gradient,stroke,stroke_width,opacity,fill_opacity,stroke_opacity,visual["dash_style"],linecap=visual["linecap"],linejoin=visual["linejoin"],filter_ref=visual["filter"],)
  return
 ctx.report.record_feature_observation(note_shape_usage(approximated=False))
 if gradient is not None:
  ctx.report.record_feature_observation(note_gradient_usage(approximated=False))
 box=ellipse_bounds(matrix,cx0,cy0,radius,radius)
 rotation=box.rotation_if_visible()if box.has_distinct_axes()else None
 rotation_style=f"{rotation:.2f}" if rotation is not None else None
 style=StyleBuilder()
 style.add_flag("ellipse").add("whiteSpace","wrap").add("html",1)
 style.add("fillColor",fill).add("strokeColor",stroke).add("strokeWidth",stroke_width)
 style.add("opacity",opacity).add("fillOpacity",fill_opacity).add("strokeOpacity",stroke_opacity)
 add_gradient_styles(style,gradient)
 style.extend_raw(visual["dash_style"])
 style.add("rotation",rotation_style,when=rotation_style is not None)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=fill if fill!="none" else stroke)
 ctx.add(make_box_vertex(ctx,style.build(),box,cell_id=elem.get("data-drawio-cell-id")))
def emit_ellipse(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str]|None=None)->None:
 visual=get_visual(elem,css)
 cx0=parse_length(elem.get("cx"))
 cy0=parse_length(elem.get("cy"))
 rx0=parse_length(elem.get("rx"))
 ry0=parse_length(elem.get("ry"))
 fill,gradient=ctx.defs.resolve_fill(visual["fill"]or "none")
 stroke=visual["stroke"]or "none"
 opacity=opacity_pct(visual["opacity"])
 fill_opacity=opacity_pct(visual["fill_opacity"])
 stroke_opacity=opacity_pct(visual["stroke_opacity"])
 stroke_width=visual["stroke_width"]*stroke_scale(matrix)
 approx_support_filter,approx_style_filter=multi_stop_filter_refs(ctx,visual["filter"])
 if supports_multi_stop_gradient_approximation("ellipse",matrix,gradient,filter_ref=approx_support_filter):
  assert gradient is not None
  ctx.report.record_feature_observation(note_shape_usage(approximated=False))
  ctx.report.record_feature_observation(note_gradient_usage(approximated=True))
  emit_multi_stop_gradient_approximation(ctx,elem,matrix,ShapeSpec("ellipse",cx0-rx0,cy0-ry0,rx0*2,ry0*2),gradient,stroke=stroke,stroke_width=stroke_width,opacity=opacity,fill_opacity=fill_opacity,stroke_opacity=stroke_opacity,dash=visual["dash_style"],filter_ref=approx_style_filter,)
  return
 if has_shear(matrix):
  ctx.report.record_feature_observation(note_shape_usage(approximated=True))
  if gradient is not None:
   ctx.report.record_feature_observation(note_gradient_usage(approximated=False))
  emit_transformed_path_stencil(ctx,elem,ellipse_path_d(cx0,cy0,rx0,ry0),matrix,fill,gradient,stroke,stroke_width,opacity,fill_opacity,stroke_opacity,visual["dash_style"],linecap=visual["linecap"],linejoin=visual["linejoin"],filter_ref=visual["filter"],)
  return
 ctx.report.record_feature_observation(note_shape_usage(approximated=False))
 if gradient is not None:
  ctx.report.record_feature_observation(note_gradient_usage(approximated=False))
 box=ellipse_bounds(matrix,cx0,cy0,rx0,ry0)
 rotation=box.rotation_if_visible()
 rotation_style=f"{rotation:.2f}" if rotation is not None else None
 style=StyleBuilder()
 style.add_flag("ellipse").add("whiteSpace","wrap").add("html",1)
 style.add("fillColor",fill).add("strokeColor",stroke).add("strokeWidth",stroke_width)
 style.add("opacity",opacity).add("fillOpacity",fill_opacity).add("strokeOpacity",stroke_opacity)
 add_gradient_styles(style,gradient)
 style.extend_raw(visual["dash_style"])
 style.add("rotation",rotation_style,when=rotation_style is not None)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=fill if fill!="none" else stroke)
 ctx.add(make_box_vertex(ctx,style.build(),box,cell_id=elem.get("data-drawio-cell-id")))
def emit_rect(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str]|None=None)->None:
 visual=get_visual(elem,css)
 x0=parse_length(elem.get("x"))
 y0=parse_length(elem.get("y"))
 width0=parse_length(elem.get("width"))
 height0=parse_length(elem.get("height"))
 if width0<=0 or height0<=0:
  return
 rx=parse_length(elem.get("rx","0"))or 0.0
 ry=parse_length(elem.get("ry","0"))or 0.0
 if rx<=0<ry:
  rx=ry
 if ry<=0<rx:
  ry=rx
 fill,gradient=ctx.defs.resolve_fill(visual["fill"]or "#ffffff")
 stroke=visual["stroke"]or "none"
 opacity=opacity_pct(visual["opacity"])
 fill_opacity=opacity_pct(visual["fill_opacity"])
 stroke_opacity=opacity_pct(visual["stroke_opacity"])
 stroke_width=visual["stroke_width"]*stroke_scale(matrix)
 approx_support_filter,approx_style_filter=multi_stop_filter_refs(ctx,visual["filter"])
 if supports_multi_stop_gradient_approximation("rect",matrix,gradient,filter_ref=approx_support_filter):
  assert gradient is not None
  ctx.report.record_feature_observation(note_shape_usage(approximated=False))
  ctx.report.record_feature_observation(note_gradient_usage(approximated=True))
  emit_multi_stop_gradient_approximation(ctx,elem,matrix,ShapeSpec("rect",x0,y0,width0,height0,rx=rx,ry=ry),gradient,stroke=stroke,stroke_width=stroke_width,opacity=opacity,fill_opacity=fill_opacity,stroke_opacity=stroke_opacity,dash=visual["dash_style"],filter_ref=approx_style_filter,)
  return
 if has_shear(matrix):
  ctx.report.record_feature_observation(note_shape_usage(approximated=True))
  if gradient is not None:
   ctx.report.record_feature_observation(note_gradient_usage(approximated=False))
  if rx>0 or ry>0:
   emit_transformed_path_stencil(ctx,elem,rounded_rect_path_d(x0,y0,width0,height0,rx,ry),matrix,fill,gradient,stroke,stroke_width,opacity,fill_opacity,stroke_opacity,visual["dash_style"],linecap=visual["linecap"],linejoin=visual["linejoin"],filter_ref=visual["filter"],)
   return
  corners=rect_corners(matrix,x0,y0,width0,height0)
  emit_polygon_stencil(ctx,elem,corners,fill,gradient,stroke,stroke_width,opacity,fill_opacity,stroke_opacity,visual["dash_style"],filter_ref=visual["filter"],)
  return
 ctx.report.record_feature_observation(note_shape_usage(approximated=False))
 if gradient is not None:
  ctx.report.record_feature_observation(note_gradient_usage(approximated=False))
 box=rect_bounds(matrix,x0,y0,width0,height0)
 rotation=box.rotation_if_visible()
 rotation_style=f"{rotation:.2f}" if rotation is not None else None
 style=StyleBuilder()
 if rx>0 or ry>0:
  shorter_side=min(width0,height0)if width0>0 and height0>0 else 1.0
  arc_pct=min(50,round(max(rx,ry)/shorter_side*100))
  style.add("rounded",1).add("arcSize",arc_pct)
 else:
  style.add("rounded",0)
 style.add("whiteSpace","wrap").add("html",1)
 style.add("fillColor",fill).add("strokeColor",stroke).add("strokeWidth",stroke_width)
 style.add("opacity",opacity).add("fillOpacity",fill_opacity).add("strokeOpacity",stroke_opacity)
 add_gradient_styles(style,gradient)
 style.extend_raw(visual["dash_style"])
 style.add("rotation",rotation_style,when=rotation_style is not None)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=fill if fill!="none" else stroke)
 ctx.add(make_box_vertex(ctx,style.build(),box,cell_id=elem.get("data-drawio-cell-id")))
"""Emitters for SVG polylines and polygons."""
import re
from xml.etree.ElementTree import Element
_POINT_RE=re.compile(r"[-\d.eE+]+")
def emit_polyline(ctx:EmitterContext,elem:Element,matrix:Matrix,closed:bool=False,css:dict[str,str]|None=None,)->None:
 visual=get_visual(elem,css)
 coords=_POINT_RE.findall(elem.get("points",""))
 points=[apply_pt(matrix,float(coords[i]),float(coords[i+1]))for i in range(0,len(coords)-1,2)]
 if len(points)<2:
  return
 ctx.report.record_feature_observation(note_shape_usage(approximated=False))
 stroke_color=visual["stroke"]or "#000000"
 fill=visual["fill"]or "none"
 opacity=opacity_pct(visual["opacity"])
 fill_opacity=opacity_pct(visual["fill_opacity"])
 stroke_opacity=opacity_pct(visual["stroke_opacity"])
 geometry_scale=stroke_scale(matrix)
 stroke_width=visual["stroke_width"]*geometry_scale
 if closed and fill!="none":
  box=bounds_from_points(points)
  first_x=(points[0][0]-box.x)/box.width*100
  first_y=(points[0][1]-box.y)/box.height*100
  path_parts=[f'<move x="{first_x:.2f}" y="{first_y:.2f}"/>']
  path_parts.extend(f'<line x="{(px-box.x)/box.width*100:.2f}" y="{(py-box.y)/box.height*100:.2f}"/>' for px,py in points[1:])
  path_parts.append("<close/>")
  xml=('<shape w="100" h="100" aspect="variable" strokewidth="inherit">' f"<background><path>{''.join(path_parts)}</path><fillstroke/></background></shape>")
  stencil_style=make_stencil_style_from_xml(xml,fill,stroke_color,stroke_width,opacity)
  if not stencil_style:
   return
  style=StyleBuilder().extend_raw(stencil_style)
  style.add("fillOpacity",fill_opacity).add("strokeOpacity",stroke_opacity)
  style.extend_raw(visual["dash_style"])
  add_metadata_styles(style,elem,ctx)
  add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=fill if fill!="none" else stroke_color)
  ctx.add(make_box_vertex(ctx,style.build(),box))
  return
 start_arrow=ctx.defs.resolve_marker(visual["marker_start"])
 end_arrow=ctx.defs.resolve_marker(visual["marker_end"])
 start_shape=ctx.defs.resolve_custom_marker_shape(visual["marker_start"])
 end_shape=ctx.defs.resolve_custom_marker_shape(visual["marker_end"])
 if start_shape is not None:
  start_arrow="none"
 if end_shape is not None:
  end_arrow="none"
 if start_arrow!="none" or end_arrow!="none" or visual.get("marker_mid")or start_shape or end_shape:
  ctx.report.record_feature_observation(note_marker_usage())
 src,*mid,tgt=points
 src,tgt=extend_edge_endpoints(src,tgt,mid,start_extension=(ctx.defs.resolve_marker_extension(visual["marker_start"],at_start=True,stroke_width=stroke_width,user_scale=geometry_scale,)if start_shape is None else 0.0),end_extension=(ctx.defs.resolve_marker_extension(visual["marker_end"],at_start=False,stroke_width=stroke_width,user_scale=geometry_scale,)if end_shape is None else 0.0),)
 style=StyleBuilder()
 style.add("rounded",1 if visual["linejoin"]=="round" else 0)
 style.add("lineCap",visual["linecap"],when=visual["linecap"]!="flat")
 style.add("lineJoin",visual["linejoin"],when=visual["linejoin"]!="miter")
 add_native_arrow_styles(style,start_arrow,end_arrow).add("html",1)
 style.add("strokeColor",stroke_color).add("strokeWidth",stroke_width)
 style.add("opacity",opacity).add("strokeOpacity",stroke_opacity)
 style.extend_raw(visual["dash_style"])
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=stroke_color)
 ctx.add(make_edge(ctx,style.build(),src,tgt,waypoints=mid))
 marker_size=max(stroke_width*4.0,8.0)
 if start_shape is not None:
  emit_endpoint_marker(ctx,src,start_shape,stroke_color,opacity,size=marker_size,rotation=segment_angle_degrees(src,mid[0]if mid else tgt)+180.0,)
 if end_shape is not None:
  emit_endpoint_marker(ctx,tgt,end_shape,stroke_color,opacity,size=marker_size,rotation=segment_angle_degrees(mid[-1]if mid else src,tgt),)
 if visual.get("marker_mid")and mid:
  emit_midpoint_markers(ctx,mid,stroke_color,opacity)
"""Emitters for SVG path elements."""
import re
from xml.etree.ElementTree import Element
def _is_closed(path_data:str|None)->bool:
 path_text=path_data or ""
 return any(char in path_text for char in("Z","z"))
def _has_curve_commands(path_data:str|None)->bool:
 return bool(re.search(r"[CcSsQqTtAa]",path_data or ""))
def _emit_open_path_as_edge(ctx:EmitterContext,elem:Element,path_data:str,matrix:Matrix,visual:VisualStyle,)->None:
 points_t=[apply_pt(matrix,px,py)for px,py in sample_open_path(path_data)]
 if len(points_t)<2:
  return
 stroke_color=visual["stroke"]or "#000000"
 opacity=opacity_pct(visual["opacity"])
 stroke_opacity=opacity_pct(visual["stroke_opacity"])
 geometry_scale=stroke_scale(matrix)
 stroke_width=visual["stroke_width"]*geometry_scale
 start_arrow=ctx.defs.resolve_marker(visual["marker_start"])
 end_arrow=ctx.defs.resolve_marker(visual["marker_end"])
 start_shape=ctx.defs.resolve_custom_marker_shape(visual["marker_start"])
 end_shape=ctx.defs.resolve_custom_marker_shape(visual["marker_end"])
 if start_shape is not None:
  start_arrow="none"
 if end_shape is not None:
  end_arrow="none"
 if start_arrow!="none" or end_arrow!="none" or visual.get("marker_mid")or start_shape or end_shape:
  ctx.report.record_feature_observation(note_marker_usage())
 ctx.report.record_feature_observation(note_shape_usage(approximated=False))
 src,*mid,tgt=points_t
 src,tgt=extend_edge_endpoints(src,tgt,mid,start_extension=(ctx.defs.resolve_marker_extension(visual["marker_start"],at_start=True,stroke_width=stroke_width,user_scale=geometry_scale,)if start_shape is None else 0.0),end_extension=(ctx.defs.resolve_marker_extension(visual["marker_end"],at_start=False,stroke_width=stroke_width,user_scale=geometry_scale,)if end_shape is None else 0.0),)
 style=StyleBuilder()
 style.add("rounded",1 if visual["linejoin"]=="round" else 0,when=not(_has_curve_commands(path_data)and mid))
 style.add("lineCap",visual["linecap"],when=visual["linecap"]!="flat")
 style.add("lineJoin",visual["linejoin"],when=visual["linejoin"]!="miter")
 style.add("curved",1,when=_has_curve_commands(path_data)and bool(mid))
 add_native_arrow_styles(style,start_arrow,end_arrow).add("html",1)
 style.add("strokeColor",stroke_color).add("strokeWidth",stroke_width)
 style.add("opacity",opacity).add("strokeOpacity",stroke_opacity)
 style.extend_raw(visual["dash_style"])
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=stroke_color)
 ctx.add(make_edge(ctx,style.build(),src,tgt,waypoints=mid,cell_id=elem.get("data-drawio-cell-id")))
 marker_size=max(stroke_width*4.0,8.0)
 if start_shape is not None:
  start_rotation=segment_angle_degrees(src,mid[0]if mid else tgt)+180.0
  emit_endpoint_marker(ctx,src,start_shape,stroke_color,opacity,size=marker_size,rotation=start_rotation)
 if end_shape is not None:
  end_rotation=segment_angle_degrees(mid[-1]if mid else src,tgt)
  emit_endpoint_marker(ctx,tgt,end_shape,stroke_color,opacity,size=marker_size,rotation=end_rotation)
 if visual.get("marker_mid")and mid:
  emit_midpoint_markers(ctx,mid,stroke_color,opacity)
def emit_path(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str]|None=None)->None:
 visual=get_visual(elem,css)
 path_data=elem.get("d","")
 if not path_data:
  return
 fill,gradient=ctx.defs.resolve_fill(visual["fill"]or "none")
 has_markers=visual["marker_start"]or visual["marker_end"]or visual["marker_mid"]
 if fill=="none" and has_markers:
  _emit_open_path_as_edge(ctx,elem,path_data,matrix,visual)
  return
 commands=path_commands(path_data,point_transform=lambda x,y:apply_pt(matrix,x,y))
 if not commands:
  return
 bbox=commands_bbox(commands)
 if not bbox:
  return
 bx,by,bw,bh=bbox
 stroke_color=visual["stroke"]or "#000000"
 opacity=opacity_pct(visual["opacity"])
 fill_opacity=opacity_pct(visual["fill_opacity"])
 stroke_opacity=opacity_pct(visual["stroke_opacity"])
 stroke_width=visual["stroke_width"]*stroke_scale(matrix)
 fill_rule=visual.get("fill_rule","nonzero")
 approx_support_filter,approx_style_filter=multi_stop_filter_refs(ctx,visual["filter"])
 if gradient is not None and supports_multi_stop_gradient_approximation("path",matrix,gradient,filter_ref=approx_support_filter):
  ctx.report.record_feature_observation(note_shape_usage(approximated=False))
  ctx.report.record_feature_observation(note_gradient_usage(approximated=True))
  emit_path_multi_stop_gradient_approximation(ctx,elem,commands,bbox,gradient,stroke=stroke_color,stroke_width=stroke_width,opacity=opacity,fill_opacity=fill_opacity,stroke_opacity=stroke_opacity,fill_rule=fill_rule,linecap=visual["linecap"],linejoin=visual["linejoin"],dash=visual["dash_style"],filter_ref=approx_style_filter,)
  return
 ctx.report.record_feature_observation(note_shape_usage(approximated=False))
 if gradient is not None:
  ctx.report.record_feature_observation(note_gradient_usage(approximated=False))
 stencil_style=make_stencil_style_from_commands(commands,bx,by,bw,bh,fill,stroke_color,stroke_width,opacity,fill_rule=fill_rule,linecap=visual["linecap"],linejoin=visual["linejoin"],)
 if not stencil_style:
  return
 style=StyleBuilder().extend_raw(stencil_style)
 style.add("fillOpacity",fill_opacity).add("strokeOpacity",stroke_opacity)
 add_gradient_styles(style,gradient)
 style.extend_raw(visual["dash_style"])
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=fill if fill!="none" else stroke_color,)
 ctx.add(make_bounds_vertex(ctx,style.build(),bx,by,bw,bh))
"""Emitters for SVG text elements."""
import html
from dataclasses import dataclass,replace
from xml.etree.ElementTree import Element
_TSPAN_STYLE_ATTRS:tuple[str,...]=("x","y","dy","dx","fill","font-size","font-weight","font-style","font-family","text-decoration","text-anchor","style",)
@dataclass(frozen=True)
class TextPathRun:
 content:str
 visual:VisualStyle
 dx:float=0.0
 dy:float=0.0
@dataclass
class StructuredTextRun:
 content:str
 visual:VisualStyle
 x:float
 y:float
 advance:float
@dataclass
class StructuredTextChunk:
 origin_x:float
 anchor:str
 end_x:float
 runs:list[StructuredTextRun]
@dataclass
class StructuredTextLayout:
 x:float
 y:float
 chunks:list[StructuredTextChunk]
 active_chunk:StructuredTextChunk|None=None
def _has_text_path(elem:Element)->bool:
 return any(strip_ns(child.tag)=="textPath" for child in elem)
def _first_text_path_child(elem:Element)->Element|None:
 return next((child for child in elem if strip_ns(child.tag)=="textPath"),None)
def _font_kwargs(visual:VisualStyle)->dict[str,str]:
 return{"font_weight":str(visual.get("font_weight","normal")or "normal"),"font_style":str(visual.get("font_style_v","normal")or "normal"),}
def _visual_to_css(visual:VisualStyle)->dict[str,str]:
 css:dict[str,str]={"fill":visual["text_fill"]or "#000000","font-size":str(visual["font_size"]),"font-family":visual.get("font_family")or "Helvetica","font-weight":str(visual.get("font_weight")or "normal"),"font-style":str(visual.get("font_style_v")or "normal"),"text-anchor":str(visual.get("text_anchor")or "start"),"text-decoration":str(visual.get("text_decoration")or "none"),"baseline-shift":str(visual.get("baseline_shift")or "0"),"dominant-baseline":str(visual.get("dominant_baseline")or "alphabetic"),"letter-spacing":str(visual.get("letter_spacing")or "normal"),}
 if visual.get("text_length")is not None:
  css["textLength"]=str(visual["text_length"])
 if visual.get("length_adjust"):
  css["lengthAdjust"]=str(visual["length_adjust"])
 return css
def _baseline_offset(font_size:float,est_height:float,visual:VisualStyle)->float:
 baseline_shift=str(visual.get("baseline_shift")or "0").strip().lower()
 if baseline_shift=="super":
  baseline_shift_px=font_size*0.35
 elif baseline_shift=="sub":
  baseline_shift_px=-font_size*0.35
 elif baseline_shift in("0","","baseline"):
  baseline_shift_px=0.0
 else:
  baseline_shift_px=parse_length(baseline_shift,0.0)
 dominant_baseline=str(visual.get("dominant_baseline")or "alphabetic").strip().lower()
 if dominant_baseline in{"middle","central"}:
  dominant_offset=est_height*0.50
 elif dominant_baseline in{"text-before-edge","before-edge","hanging"}:
  dominant_offset=est_height*0.18
 elif dominant_baseline in{"text-after-edge","after-edge","ideographic"}:
  dominant_offset=est_height*0.95
 else:
  dominant_offset=max(font_size*0.85,est_height*0.60)
 return dominant_offset+baseline_shift_px
def _path_center_offset(font_size:float,visual:VisualStyle)->float:
 reference_height=max(font_size,1.0)
 return _baseline_offset(font_size,reference_height,visual)-reference_height/2.0
def _letter_spacing_px(visual:VisualStyle)->float:
 letter_spacing=str(visual.get("letter_spacing")or "normal").strip().lower()
 if letter_spacing in{"","normal"}:
  return 0.0
 return parse_length(letter_spacing,0.0)
def _glyph_run_layout(content:str,visual:VisualStyle,metrics_policy:str,)->tuple[list[str],list[float],float,float,str]:
 font_size=max(visual["font_size"],6)
 font_family=visual.get("font_family")or "Helvetica"
 font_kwargs=_font_kwargs(visual)
 glyphs=list(content)
 _,_,backend=measure_text_detailed(content,font_size,font_family,policy=metrics_policy,**font_kwargs,)
 if not glyphs:
  return[],[],0.0,0.0,backend
 glyph_widths=[measure_text(glyph if glyph.strip()else " ",font_size,font_family,policy=metrics_policy,**font_kwargs)[0]for glyph in glyphs]
 gap_count=max(len(glyphs)-1,0)
 gap_spacing=_letter_spacing_px(visual)
 target_length=visual.get("text_length")
 length_adjust=str(visual.get("length_adjust")or "spacing").strip().lower()
 if target_length is not None and gap_count>0:
  if length_adjust=="spacingandglyphs":
   current_length=sum(glyph_widths)+gap_spacing*gap_count
   if current_length>1e-6:
    scale=target_length/current_length
    glyph_widths=[max(width*scale,font_size*0.1)for width in glyph_widths]
    gap_spacing*=scale
  else:
   gap_spacing+=(target_length-sum(glyph_widths)-gap_spacing*gap_count)/gap_count
 elif target_length is not None and gap_count==0:
  glyph_widths=[max(target_length,glyph_widths[0])]
 total_width=sum(glyph_widths)+gap_spacing*gap_count
 return glyphs,glyph_widths,gap_spacing,total_width,backend
def _extend_text_path_runs(runs:list[TextPathRun],node:Element,node_visual:VisualStyle,node_css:dict[str,str]|None,ctx:EmitterContext,*,leading_dx:float=0.0,leading_dy:float=0.0,)->None:
 content=node.text or ""
 if content or abs(leading_dx)>1e-6 or abs(leading_dy)>1e-6:
  runs.append(TextPathRun(content,node_visual,dx=leading_dx,dy=leading_dy))
 current_css=node_css or _visual_to_css(node_visual)
 for child in node:
  if strip_ns(child.tag)!="tspan":
   continue
  child_visual=_tspan_visual(child,current_css,ctx)
  _extend_text_path_runs(runs,child,child_visual,_visual_to_css(child_visual),ctx,leading_dx=parse_length(child.get("dx"),0.0),leading_dy=parse_length(child.get("dy"),0.0),)
  if child.tail:
   runs.append(TextPathRun(child.tail,node_visual))
def _collect_text_path_runs(text_path:Element,visual:VisualStyle,css:dict[str,str]|None,ctx:EmitterContext,)->list[TextPathRun]:
 runs:list[TextPathRun]=[]
 _extend_text_path_runs(runs,text_path,visual,css,ctx)
 return runs
def _should_emit_positioned_glyphs(content:str,visual:VisualStyle)->bool:
 return "\n" not in content and(abs(_letter_spacing_px(visual))>1e-6 or visual.get("text_length")is not None)
def _run_advance(content:str,visual:VisualStyle,metrics_policy:str)->float:
 font_size=max(visual["font_size"],6)
 font_family=visual.get("font_family")or "Helvetica"
 font_kwargs=_font_kwargs(visual)
 if not _should_emit_positioned_glyphs(content,visual):
  advance,_=measure_text(content,font_size,font_family,policy=metrics_policy,**font_kwargs)
  return advance
 _,_,_,total_width,_=_glyph_run_layout(content,visual,metrics_policy)
 return total_width
def _emit_text_cell(ctx:EmitterContext,elem:Element,matrix:Matrix,visual:VisualStyle,x0:float,y0:float,content:str,*,record_backend:bool=True,minimum_width:float=20.0,width_override:float|None=None,align_override:str|None=None,rotation_degrees:float|None=None,placement:str="baseline",cell_id:str|None=None,)->None:
 font_color=visual["text_fill"]or "#000000"
 font_size=max(visual["font_size"],6)
 font_family=visual.get("font_family")or "Helvetica"
 metrics_policy=ctx.rendering_options.text_metrics_policy
 opacity=opacity_pct(visual["opacity"]*visual["text_opacity"])
 align=align_override or{"start":"left","middle":"center","end":"right"}.get(visual["text_anchor"],"left")
 font_style=font_style_flag(visual)
 x,y=apply_pt(matrix,x0,y0)
 est_width,est_height,backend=measure_text_detailed(content,font_size,font_family,policy=metrics_policy,**_font_kwargs(visual),)
 if record_backend:
  ctx.report.record_feature_observation(note_text_backend(backend))
 est_width=max(width_override if width_override is not None else est_width,minimum_width)
 tx=x-(est_width/2 if align=="center" else est_width if align=="right" else 0)
 if placement=="center":
  ty=y-est_height/2.0
 else:
  ty=y-_baseline_offset(font_size,est_height,visual)
 rotation_style=f"{rotation_degrees:.2f}" if rotation_degrees is not None else None
 style=StyleBuilder()
 style.add_flag("text").add("html",1).add("strokeColor","none").add("fillColor","none")
 style.add("align",align).add("verticalAlign","middle").add("whiteSpace","wrap").add("rounded",0)
 style.add("fontSize",font_size).add("fontColor",font_color).add("fontFamily",font_family)
 style.add("opacity",opacity).add("fontStyle",font_style)
 style.add("rotation",rotation_style,when=rotation_style is not None)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=font_color)
 escaped_content=html.escape(content)
 ctx.add(make_bounds_vertex(ctx,style.build(),tx,ty,est_width,est_height,value=escaped_content,cell_id=cell_id))
def _emit_positioned_glyphs(ctx:EmitterContext,elem:Element,matrix:Matrix,visual:VisualStyle,x0:float,y0:float,content:str,)->float:
 metrics_policy=ctx.rendering_options.text_metrics_policy
 font_size=max(visual["font_size"],6)
 glyphs,glyph_widths,gap_spacing,total_width,backend=_glyph_run_layout(content,visual,metrics_policy)
 ctx.report.record_feature_observation(note_text_backend(backend))
 if not glyphs:
  return 0.0
 align={"start":"left","middle":"center","end":"right"}.get(visual["text_anchor"],"left")
 start_x=x0-(total_width/2 if align=="center" else total_width if align=="right" else 0.0)
 child_cells:list[Cell]=[]
 group_id=ctx.next_id()
 child_ctx=replace(ctx.with_parent(group_id),add_cell=child_cells.append)
 cursor_x=start_x
 emitted_count=0
 for index,(glyph,glyph_width)in enumerate(zip(glyphs,glyph_widths,strict=True)):
  if glyph.strip():
   _emit_text_cell(child_ctx,elem,matrix,visual,cursor_x,y0,glyph,record_backend=False,minimum_width=max(font_size*0.25,1.0),width_override=glyph_width,)
   emitted_count+=1
  cursor_x+=glyph_width
  if index<len(glyphs)-1:
   cursor_x+=gap_spacing
 direct_children=[cell for cell in child_cells if cell.parent==group_id]
 if emitted_count<=1 or not direct_children:
  for cell in child_cells:
   ctx.add(cell)
  return total_width
 gx,gy,gw,gh=group_bbox(direct_children)
 ctx.add(make_bounds_vertex(ctx,"group;",gx,gy,gw,gh,cell_id=group_id))
 shift_cells(direct_children,gx,gy)
 for cell in child_cells:
  ctx.add(cell)
 return total_width
def _emit_text_along_path(ctx:EmitterContext,elem:Element,matrix:Matrix,visual:VisualStyle,text_path:Element,runs:list[TextPathRun],)->bool:
 href=text_path.get("href")or text_path.get("{http://www.w3.org/1999/xlink}href")or ""
 if not href.startswith("#"):
  return False
 path_id=href[1:]
 path_elem=ctx.defs.get_element(path_id)
 if path_elem is None or strip_ns(path_elem.tag)!="path":
  return False
 path_data=path_elem.get("d")
 if not path_data:
  return False
 path_matrix=ctx.defs.get_element_transform(path_id)or[1.0,0.0,0.0,1.0,0.0,0.0]
 polyline=sample_path_polyline(path_data,point_transform=lambda px,py:apply_pt(path_matrix,px,py),curve_steps=80,)
 total_path_length=polyline_length(polyline)
 if len(polyline)<2 or total_path_length<=1e-6:
  return False
 metrics_policy=ctx.rendering_options.text_metrics_policy
 run_layouts:list[tuple[TextPathRun,list[str],list[float],float,float]]=[]
 total_advance=0.0
 emitted_content=False
 for run in runs:
  glyphs,glyph_widths,gap_spacing,total_width,backend=_glyph_run_layout(run.content,run.visual,metrics_policy,)
  ctx.report.record_feature_observation(note_text_backend(backend))
  emitted_content=emitted_content or any(glyph.strip()for glyph in glyphs)
  run_layouts.append((run,glyphs,glyph_widths,gap_spacing,total_width))
  total_advance+=run.dx+total_width
 if not emitted_content:
  return False
 advance_scale=1.0
 if total_advance>total_path_length and total_advance>1e-6:
  advance_scale=total_path_length/total_advance
  total_advance=total_path_length
 start_distance=parse_start_offset(text_path.get("startOffset"),total_path_length)
 anchor={"start":"left","middle":"center","end":"right"}.get(visual["text_anchor"],"left")
 if anchor=="center":
  start_distance-=total_advance/2.0
 elif anchor=="right":
  start_distance-=total_advance
 child_cells:list[Cell]=[]
 group_id=ctx.next_id()
 child_ctx=replace(ctx.with_parent(group_id),add_cell=child_cells.append)
 cursor_distance=0.0
 emitted_count=0
 for run,glyphs,glyph_widths,gap_spacing,total_width in run_layouts:
  font_size=max(run.visual["font_size"],6)
  cursor_distance+=run.dx*advance_scale
  path_glyph_widths=[width*advance_scale for width in glyph_widths]
  path_gap_spacing=gap_spacing*advance_scale
  for index,(glyph,glyph_width,path_glyph_width)in enumerate(zip(glyphs,glyph_widths,path_glyph_widths,strict=True)):
   center_distance=start_distance+cursor_distance+path_glyph_width/2.0
   path_position=point_and_angle_at_distance(polyline,center_distance)
   if glyph.strip()and path_position is not None:
    baseline_to_center=_path_center_offset(font_size,run.visual)
    normal_x,normal_y=normal_vector(path_position.angle_degrees)
    center_x=path_position.x-normal_x*baseline_to_center+normal_x*run.dy
    center_y=path_position.y-normal_y*baseline_to_center+normal_y*run.dy
    _emit_text_cell(child_ctx,elem,matrix,run.visual,center_x,center_y,glyph,record_backend=False,minimum_width=max(font_size*0.25,1.0),width_override=glyph_width,align_override="center",rotation_degrees=path_position.angle_degrees,placement="center",)
    emitted_count+=1
   cursor_distance+=path_glyph_width
   if index<len(glyphs)-1:
    cursor_distance+=path_gap_spacing
 direct_children=[cell for cell in child_cells if cell.parent==group_id]
 if emitted_count<=1 or not direct_children:
  for cell in child_cells:
   ctx.add(cell)
  return emitted_count>0
 gx,gy,gw,gh=group_bbox(direct_children)
 ctx.add(make_bounds_vertex(ctx,"group;",gx,gy,gw,gh,cell_id=group_id))
 shift_cells(direct_children,gx,gy)
 for cell in child_cells:
  ctx.add(cell)
 return True
def _emit_text_run(ctx:EmitterContext,elem:Element,matrix:Matrix,visual:VisualStyle,x0:float,y0:float,content:str,)->float:
 if not content:
  return 0.0
 if _should_emit_positioned_glyphs(content,visual):
  return _emit_positioned_glyphs(ctx,elem,matrix,visual,x0,y0,content)
 _emit_text_cell(ctx,elem,matrix,visual,x0,y0,content)
 return _run_advance(content,visual,ctx.rendering_options.text_metrics_policy)
def _collect_text(elem:Element)->str:
 return "".join(elem.itertext()).strip()
def _has_structured_tspans(elem:Element)->bool:
 for child in elem.iter():
  if child is elem or strip_ns(child.tag)!="tspan":
   continue
  if child.get("class")or child.get("id")or any(child.get(attr)for attr in _TSPAN_STYLE_ATTRS):
   return True
  if any(strip_ns(grandchild.tag)=="tspan" for grandchild in child):
   return True
 return False
def _tspan_visual(tspan:Element,parent_css:dict[str,str]|None,ctx:EmitterContext|None=None,)->VisualStyle:
 if ctx is not None:
  pass
  computed=apply_css(tspan,ctx.css_rules,"tspan",parent_css,custom_props=ctx.custom_props,rule_index=ctx.rule_index)
 else:
  computed=dict(parent_css or{})
  computed.update(parse_style_attr(tspan.get("style","")))
  for attr in("fill","font-size","font-weight","font-style","font-family","text-decoration","text-anchor",):
   value=tspan.get(attr)
   if value:
    computed[attr]=value
 return get_visual(tspan,computed)
def _advance_for_whitespace(raw:str,visual:VisualStyle,metrics_policy:str,)->float:
 if not raw:
  return 0.0
 font_size=max(visual["font_size"],6)
 font_family=visual.get("font_family")or "Helvetica"
 font_kwargs=_font_kwargs(visual)
 spaced_width,_=measure_text("a a",font_size,font_family,policy=metrics_policy,**font_kwargs,)
 compact_width,_=measure_text("aa",font_size,font_family,policy=metrics_policy,**font_kwargs,)
 return max(spaced_width-compact_width,font_size*0.2)
def _text_coordinate(value:str|None,font_size:float,default:float=0.0)->float:
 if value is None:
  return default
 text=value.strip().lower()
 if text.endswith("em"):
  return parse_length(text[:-2],default/font_size if font_size else 0.0)*font_size
 if text.endswith("ex"):
  return parse_length(text[:-2],default/(font_size*0.5)if font_size else 0.0)*font_size*0.5
 return parse_length(value,default)
def _layout_text_fragment(ctx:EmitterContext,layout:StructuredTextLayout,visual:VisualStyle,raw:str|None,)->None:
 if not raw:
  return
 content=raw.strip()
 if not content:
  if "\n" in raw or "\r" in raw:
   return
  whitespace_advance=_advance_for_whitespace(raw,visual,ctx.rendering_options.text_metrics_policy)
  layout.x+=whitespace_advance
  if layout.active_chunk is not None:
   layout.active_chunk.end_x=layout.x
  return
 metrics_policy=ctx.rendering_options.text_metrics_policy
 if layout.active_chunk is None:
  layout.active_chunk=StructuredTextChunk(origin_x=layout.x,anchor=str(visual.get("text_anchor")or "start"),end_x=layout.x,runs=[],)
  layout.chunks.append(layout.active_chunk)
 if raw[0].isspace():
  layout.x+=_advance_for_whitespace(raw,visual,metrics_policy)
 advance=_run_advance(content,visual,metrics_policy)
 layout.active_chunk.runs.append(StructuredTextRun(content=content,visual=visual,x=layout.x,y=layout.y,advance=advance,))
 layout.x+=advance
 if raw[-1].isspace():
  layout.x+=_advance_for_whitespace(raw,visual,metrics_policy)
 layout.active_chunk.end_x=layout.x
def _layout_tspan_subtree(ctx:EmitterContext,node:Element,node_visual:VisualStyle,node_css:dict[str,str]|None,layout:StructuredTextLayout,)->None:
 _layout_text_fragment(ctx,layout,node_visual,node.text)
 inherited_css=node_css or _visual_to_css(node_visual)
 for child in node:
  if strip_ns(child.tag)!="tspan":
   continue
  child_visual=_tspan_visual(child,inherited_css,ctx)
  child_font_size=max(child_visual["font_size"],1.0)
  starts_new_chunk=child.get("x")is not None or child.get("y")is not None
  if child.get("x")is not None:
   layout.x=_text_coordinate(child.get("x"),child_font_size)
  if child.get("y")is not None:
   layout.y=_text_coordinate(child.get("y"),child_font_size)
  if starts_new_chunk:
   layout.active_chunk=None
  layout.x+=_text_coordinate(child.get("dx"),child_font_size)
  layout.y+=_text_coordinate(child.get("dy"),child_font_size)
  _layout_tspan_subtree(ctx,child,child_visual,_visual_to_css(child_visual),layout,)
  _layout_text_fragment(ctx,layout,node_visual,child.tail)
def _emit_structured_text(ctx:EmitterContext,elem:Element,matrix:Matrix,visual:VisualStyle,css:dict[str,str]|None,x0:float,y0:float,)->None:
 layout=StructuredTextLayout(x=x0,y=y0,chunks=[])
 _layout_tspan_subtree(ctx,elem,visual,css,layout)
 for chunk in layout.chunks:
  chunk_width=max(chunk.end_x-chunk.origin_x,0.0)
  if chunk.anchor=="middle":
   anchor_shift=-chunk_width/2.0
  elif chunk.anchor=="end":
   anchor_shift=-chunk_width
  else:
   anchor_shift=0.0
  for run in chunk.runs:
   run_visual=VisualStyle(**run.visual)
   run_visual["text_anchor"]="start"
   _emit_text_run(ctx,elem,matrix,run_visual,run.x+anchor_shift,run.y,run.content,)
def emit_text(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str]|None=None)->None:
 visual=get_visual(elem,css)
 x0=parse_length(elem.get("x"))
 y0=parse_length(elem.get("y"))
 source_id=elem.get("id")or ""
 cell_id=elem.get("data-drawio-cell-id")or(source_id if source_id.startswith(("unitlabel-","unitspec-","nodelabel-","nodeval-"))else None)
 if abs(_letter_spacing_px(visual))>1e-6:
  ctx.report.add_issue(LETTER_SPACING_APPROXIMATED,"warning","Letter spacing was approximated with positioned editable glyphs.",element_tag=strip_ns(elem.tag),element_id=elem.get("id"),)
 if visual.get("text_length")is not None:
  ctx.report.add_issue(TEXT_LENGTH_APPROXIMATED,"warning","SVG textLength constraints were approximated with positioned editable glyphs.",element_tag=strip_ns(elem.tag),element_id=elem.get("id"),)
 dominant_baseline=str(visual.get("dominant_baseline")or "alphabetic").strip().lower()
 if dominant_baseline not in{"","auto","alphabetic","baseline"}:
  ctx.report.add_issue(DOMINANT_BASELINE_APPROXIMATED,"warning","Dominant-baseline alignment was approximated for editable draw.io text.",element_tag=strip_ns(elem.tag),element_id=elem.get("id"),)
 text_path=_first_text_path_child(elem)
 if text_path is not None:
  if cell_id is not None:
   raise ValueError(f"Explicit unit label ID {cell_id!r} cannot be preserved on textPath content")
  runs=_collect_text_path_runs(text_path,visual,css,ctx)
  if not any(run.content.strip()for run in runs):
   return
  ctx.report.add_issue(TEXT_PATH_APPROXIMATED,"warning","Text on a path was approximated with rotated editable glyphs placed along the source path.",element_tag=strip_ns(elem.tag),element_id=elem.get("id"),)
  if not _emit_text_along_path(ctx,elem,matrix,visual,text_path,runs):
   content=_collect_text(elem)
   _emit_text_run(ctx,elem,matrix,visual,x0,y0,content)
  return
 if _has_structured_tspans(elem):
  if cell_id is not None:
   raise ValueError(f"Explicit unit label ID {cell_id!r} requires a single text run")
  _emit_structured_text(ctx,elem,matrix,visual,css,x0,y0)
  return
 content=_collect_text(elem)
 if not content:
  return
 if cell_id is not None:
  if _should_emit_positioned_glyphs(content,visual):
   raise ValueError(f"Explicit unit label ID {cell_id!r} cannot be preserved on positioned glyphs")
  _emit_text_cell(ctx,elem,matrix,visual,x0,y0,content,cell_id=cell_id)
 else:
  _emit_text_run(ctx,elem,matrix,visual,x0,y0,content)
"""Emitters and helpers for SVG image elements."""
import base64
import binascii
import mimetypes
import re
from os import path
from urllib.parse import quote,quote_from_bytes,unquote_to_bytes,urlparse
from xml.etree.ElementTree import Element
_ALLOWED_REMOTE_SCHEMES=frozenset({"http","https"})
_CONTROL_CHAR_RE=re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WINDOWS_DRIVE_PATH_RE=re.compile(r"^[A-Za-z]:[\\/]")
def _data_uri_from_bytes(mime:str,raw_bytes:bytes)->str:
 return f"data:{mime},{quote_from_bytes(raw_bytes,safe='')}"
def _base64_data_uri_from_bytes(mime:str,raw_bytes:bytes)->str:
 payload=base64.b64encode(raw_bytes).decode("ascii")
 return f"data:{mime};base64,{payload}"
def _normalize_data_uri(href:str)->tuple[str|None,str|None]:
 try:
  header,payload=href.split(",",1)
 except ValueError:
  return None,None
 media=header[5:]or "text/plain"
 media_type=(media.split(";",1)[0]or "text/plain").lower()
 if not media_type.startswith("image/"):
  return None,media_type
 if ";base64" in media.lower():
  try:
   raw=base64.b64decode(payload)
  except(ValueError,binascii.Error):
   return None,None
 else:
  raw=unquote_to_bytes(payload)
 if media_type=="image/svg+xml":
  return _data_uri_from_bytes(media_type,raw),media_type
 return _base64_data_uri_from_bytes(media_type,raw),media_type
def _escape_xml_attr(value:str)->str:
 return value.replace("&","&amp;").replace('"',"&quot;").replace("<","&lt;").replace(">","&gt;")
def _svg_wrapper_data_uri(image_ref:str,width:float,height:float,preserve:str)->str:
 preserve=preserve or "xMidYMid meet"
 safe_href=_escape_xml_attr(image_ref)
 safe_preserve=_escape_xml_attr(preserve)
 svg=('<?xml version="1.0" encoding="UTF-8"?>\n' f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.6f} {height:.6f}" 'f'width="{width:.6f}" height="{height:.6f}">\n'f'  <image href="{safe_href}" x="0" y="0" width="{width:.6f}" height="{height:.6f}" 'f'preserveAspectRatio="{safe_preserve}"/>\n' "</svg>\n")
 return _data_uri_from_bytes("image/svg+xml",svg.encode("utf-8"))
def emit_embedded_image_uri(ctx:EmitterContext,elem:Element,*,image_ref:str,box:BoundsBox,opacity:int=100,aspect_style:int=1,)->None:
 style=StyleBuilder()
 style.add("shape","image").add("html",1).add("image",image_ref).add("aspect","fixed")
 style.add("imageAspect",aspect_style).add("opacity",opacity)
 style.add("strokeColor","none").add("fillColor","none")
 add_metadata_styles(style,elem,ctx)
 ctx.add(make_box_vertex(ctx,style.build(),box))
def _resolve_image_href(ctx:EmitterContext,href:str|None)->tuple[str|None,str|None]:
 if not href:
  return None,None
 href=href.strip()
 if not href or _CONTROL_CHAR_RE.search(href):
  if href:
   ctx.report.add_asset(href=href,status="rejected",message="Image href contains control characters.")
  return None,None
 if href.lower().startswith("data:"):
  image_ref,mime=_normalize_data_uri(href)
  if image_ref:
   ctx.report.add_asset(href=href,status="embedded",mime_type=mime)
  elif mime and not mime.startswith("image/"):
   ctx.report.add_asset(href=href,status="rejected",mime_type=mime,message="Image data URI must use an image/* media type.",)
  else:
   ctx.report.add_asset(href=href,status="invalid",message="Invalid image data URI.")
  return image_ref,mime
 parsed=None
 if not _WINDOWS_DRIVE_PATH_RE.match(href):
  try:
   parsed=urlparse(href)
  except ValueError:
   ctx.report.add_asset(href=href,status="rejected",message="Malformed remote image URL.")
   return None,None
 if parsed is not None and parsed.scheme:
  scheme=parsed.scheme.lower()
  if scheme not in _ALLOWED_REMOTE_SCHEMES:
   ctx.report.add_asset(href=href,status="rejected",message=f"Remote image URL scheme '{scheme}' is not allowed.",)
   return None,None
  safe_href=quote(href,safe="/:#?&=%+,-._~[]@!$'()*")
  mime=mimetypes.guess_type(parsed.path)[0]or ""
  ctx.report.add_asset(href=href,status="remote",mime_type=mime or None)
  ctx.report.add_issue(IMAGE_REMOTE_LINKED,"warning","Remote image URLs stay linked instead of being embedded into the draw.io document.",element_tag="image",)
  return safe_href,mime
 if _WINDOWS_DRIVE_PATH_RE.match(href)and path.sep!="\\":
  ctx.report.add_asset(href=href,status="rejected",message="Windows absolute image paths are not supported on this platform.",)
  return None,None
 if not ctx.source_dir:
  ctx.report.add_asset(href=href,status="rejected",message="Local image references require a trusted base directory (base_dir parameter).",)
  return None,None
 asset_path=href
 if not path.isabs(asset_path):
  asset_path=path.join(ctx.source_dir,asset_path)
 asset_path=path.realpath(path.abspath(path.normpath(asset_path)))
 base_dir=path.realpath(path.abspath(ctx.source_dir))
 try:
  if path.commonpath([base_dir,asset_path])!=base_dir:
   ctx.report.add_asset(href=href,status="rejected",resolved_path=asset_path,message="Local image is outside the source directory tree.",)
   return None,None
 except ValueError:
  ctx.report.add_asset(href=href,status="rejected",resolved_path=asset_path,message="Local image could not be resolved safely.",)
  return None,None
 if not path.isfile(asset_path):
  ctx.report.add_asset(href=href,status="missing",resolved_path=asset_path,message="Local image file does not exist.",)
  return None,None
 mime=mimetypes.guess_type(asset_path)[0]or "application/octet-stream"
 with open(asset_path,"rb")as handle:
  raw=handle.read()
 ctx.report.add_dependency(asset_path)
 ctx.report.add_asset(href=href,status="embedded",resolved_path=asset_path,mime_type=mime)
 if mime=="image/svg+xml":
  return _data_uri_from_bytes(mime,raw),mime
 return _base64_data_uri_from_bytes(mime,raw),mime
def emit_image(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str]|None=None)->None:
 href=elem.get("href")or elem.get("{http://www.w3.org/1999/xlink}href")or ""
 image_ref,mime=_resolve_image_href(ctx,href)
 if not image_ref:
  return
 x0=parse_length(elem.get("x"))
 y0=parse_length(elem.get("y"))
 width0=parse_length(elem.get("width"))
 height0=parse_length(elem.get("height"))
 if width0<=0 or height0<=0:
  return
 visual=get_visual(elem,css)
 opacity=opacity_pct(visual["opacity"])
 box=image_bounds(matrix,x0,y0,width0,height0)
 rotation=box.rotation_if_visible()
 rotation_style=f"{rotation:.2f}" if rotation is not None else None
 if has_shear(matrix):
  ctx.report.add_issue(IMAGE_SHEAR_APPROXIMATED,"warning","A sheared image was approximated with its transformed bounding box because draw.io images do not skew.",element_tag="image",element_id=elem.get("id"),)
  ctx.report.record_feature_observation(note_image_usage(approximated=True))
 else:
  ctx.report.record_feature_observation(note_image_usage(approximated=False))
 preserve_raw=(elem.get("preserveAspectRatio")or "").strip()
 preserve=preserve_raw.lower()
 aspect_style=0 if preserve=="none" else 1
 if mime and mime!="image/svg+xml":
  image_ref=_svg_wrapper_data_uri(image_ref,width0,height0,preserve_raw)
 style=StyleBuilder()
 style.add("shape","image").add("html",1).add("image",image_ref).add("aspect","fixed")
 style.add("imageAspect",aspect_style).add("opacity",opacity)
 style.add("strokeColor","none").add("fillColor","none")
 style.add("rotation",rotation_style,when=rotation_style is not None)
 add_metadata_styles(style,elem,ctx)
 ctx.add(make_box_vertex(ctx,style.build(),box))
"""Emit textual SVG foreignObject content as editable draw.io labels."""
import html
import re
from xml.etree.ElementTree import Element
_BLOCK_TAGS:frozenset[str]=frozenset({"address","article","aside","blockquote","div","footer","h1","h2","h3","h4","h5","h6","header","li","main","nav","p","pre","section",})
def _extract_lines(elem:Element)->list[str]:
 lines:list[str]=[]
 current:list[str]=[]
 def flush_line()->None:
  text=re.sub(r"\s+"," ","".join(current)).strip()
  if text:
   lines.append(text)
  current.clear()
 def visit(node:Element,*,root:bool=False)->None:
  tag=strip_ns(node.tag).lower()
  is_block=tag in _BLOCK_TAGS
  if is_block and not root and current:
   flush_line()
  if tag=="br":
   flush_line()
   return
  if node.text:
   current.append(node.text)
  for child in node:
   visit(child)
   if child.tail:
    current.append(child.tail)
  if is_block and not root:
   flush_line()
 visit(elem,root=True)
 flush_line()
 return lines
def _resolve_visual(ctx:EmitterContext,elem:Element,inherited_css:dict[str,str]|None,)->tuple[VisualStyle,dict[str,str]]:
 selected_elem=elem
 selected_css=dict(inherited_css or{})
 def visit(node:Element,parent_css:dict[str,str],ancestors:list[AncestorInfo],)->bool:
  nonlocal selected_elem,selected_css
  tag=strip_ns(node.tag)
  computed=apply_css(node,ctx.css_rules,tag,parent_css,ancestors=ancestors,custom_props=ctx.custom_props,rule_index=ctx.rule_index,)
  if computed.get("color")and not computed.get("fill"):
   computed["fill"]=computed["color"]
  if node.text and node.text.strip():
   selected_elem=node
   selected_css=computed
   return True
  child_ancestors=ancestors+[ancestor_info(node)]
  return any(visit(child,computed,child_ancestors)for child in node)
 visit(elem,selected_css,[])
 return get_visual(selected_elem,selected_css),selected_css
def emit_foreign_object(ctx:EmitterContext,elem:Element,matrix:Matrix,css:dict[str,str]|None=None,)->None:
 lines=_extract_lines(elem)
 if not lines:
  return
 ctx.report.add_issue(FOREIGN_OBJECT_TEXT_APPROXIMATED,"warning","HTML text inside foreignObject was flattened to editable draw.io text; complex HTML layout may be simplified.",element_tag=strip_ns(elem.tag),element_id=elem.get("id"),)
 visual,html_css=_resolve_visual(ctx,elem,css)
 content="<br>".join(html.escape(line)for line in lines)
 font_size=max(visual["font_size"],6)
 font_family=visual.get("font_family")or "Helvetica"
 measured_width,measured_height,backend=measure_text_detailed(max(lines,key=len),font_size,font_family,policy=ctx.rendering_options.text_metrics_policy,**_font_kwargs(visual),)
 ctx.report.record_feature_observation(note_text_backend(backend))
 x=parse_length(elem.get("x"),0.0)
 y=parse_length(elem.get("y"),0.0)
 width=parse_length(elem.get("width"),measured_width)or measured_width
 height=parse_length(elem.get("height"),measured_height*len(lines))or measured_height*len(lines)
 box=image_bounds(matrix,x,y,width,height)
 text_align=(html_css.get("text-align")or "").strip().lower()
 align={"center":"center","right":"right","end":"right"}.get(text_align,"left")
 font_color=visual["text_fill"]or html_css.get("color")or "#000000"
 style=StyleBuilder()
 style.add_flag("text").add("html",1).add("strokeColor","none").add("fillColor","none")
 style.add("align",align).add("verticalAlign","middle").add("whiteSpace","wrap").add("rounded",0)
 style.add("fontSize",font_size).add("fontColor",font_color).add("fontFamily",font_family)
 style.add("opacity",opacity_pct(visual["opacity"]*visual["text_opacity"]))
 style.add("fontStyle",font_style_flag(visual))
 rotation=box.rotation_if_visible()
 rotation_style=f"{rotation:.2f}" if rotation is not None else None
 style.add("rotation",rotation_style,when=rotation_style is not None)
 add_metadata_styles(style,elem,ctx)
 add_filter_styles(style,ctx,elem,visual["filter"],fallback_color=font_color)
 ctx.add(make_box_vertex(ctx,style.build(),box,value=content))
