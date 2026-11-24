from __future__ import annotations
"""Shared diagnostic issue codes used by emitters and `capability_registry.py`.

Each code used to be duplicated as a literal string at every `_record_issue`/`add_issue`
call site *and* again as a dict key in `capability_registry.py`'s `_ISSUE_MAP`. A typo or
a renamed code in only one of those places compiled and ran fine, but silently dropped the
issue from the compatibility matrix (`issue_observation_payload` falls through to `None`
for an unmapped code). Importing these constants instead means a rename that misses a call
site raises `ImportError`/`AttributeError` immediately instead of failing silently.
"""
CLIP_PATH_SIMPLIFIED_NATIVE="clip-path-simplified-native"
MASK_SIMPLIFIED_NATIVE="mask-simplified-native"
PATTERN_SIMPLIFIED_NATIVE="pattern-simplified-native"
CLIP_PATH_FALLBACK="clip-path-fallback"
MASK_FALLBACK="mask-fallback"
PATTERN_FALLBACK="pattern-fallback"
FILTER_FALLBACK="filter-fallback"
FILTER_IGNORED_FOR_EDITABILITY="filter-ignored-for-editability"
FILTER_SIMPLIFIED_NATIVE="filter-simplified-native"
MULTI_STOP_GRADIENT_FALLBACK="multi-stop-gradient-fallback"
MULTI_STOP_GRADIENT_REDUCED="multi-stop-gradient-reduced"
TEXT_BACKEND_HEURISTIC="text-backend-heuristic"
TEXT_BACKEND_SYSTEM="text-backend-system"
TEXT_PATH_APPROXIMATED="text-path-approximated"
DOMINANT_BASELINE_APPROXIMATED="dominant-baseline-approximated"
LETTER_SPACING_IGNORED="letter-spacing-ignored"
LETTER_SPACING_APPROXIMATED="letter-spacing-approximated"
TEXT_LENGTH_APPROXIMATED="text-length-approximated"
FOREIGN_OBJECT_TEXT_APPROXIMATED="foreign-object-text-approximated"
IMAGE_SHEAR_APPROXIMATED="image-shear-approximated"
IMAGE_REMOTE_LINKED="image-remote-linked"
MAX_ELEMENTS_TRUNCATED="max-elements-truncated"
FALLBACK_BOUNDS_MISSING="fallback-bounds-missing"
USE_CYCLE_DETECTED="use-cycle-detected"
IGNORED_UNSUPPORTED_ELEMENT="ignored-unsupported-element"
CONVERSION_FAILED="conversion-failed"
ANALYSIS_FAILED="analysis-failed"
"""Shared type aliases for SVG path parsing and serialization."""
Point2D=tuple[float,float]
BezierCurve=tuple[float,float,float,float,float,float]
PathCommand=tuple[str,tuple[Point2D,...]]
"""Shared parsing and style helpers used across the converter."""
import re
from typing import Any
from urllib.parse import quote,urlparse
from xml.etree.ElementTree import Element
_UNIT_TO_PX:dict[str,float]={"px":1.0,"pt":4.0/3.0,"pc":16.0,"in":96.0,"cm":96.0/2.54,"mm":96.0/25.4,}
_SAFE_LINK_SCHEMES=frozenset({"http","https","mailto","ftp","ftps","tel","sms"})
_CONTROL_CHAR_RE=re.compile(r"[\x00-\x1f\x7f-\x9f]")
def strip_ns(tag:str)->str:
 return tag.split("}",1)[1]if "}" in tag else tag
def parse_float(value:Any,default:float=0.0)->float:
 if value is None:
  return default
 try:
  cleaned=re.sub(r"[^\d.eE+\-]","",str(value))
  return float(cleaned)if cleaned else default
 except ValueError:
  return default
def parse_length(value:Any,default:float=0.0)->float:
 if value is None:
  return default
 text=str(value).strip()
 for unit,factor in _UNIT_TO_PX.items():
  if text.endswith(unit):
   try:
    return float(text[:-len(unit)].strip())*factor
   except ValueError:
    return default
 return parse_float(value,default)
def parse_style_attr(style:str|None)->dict[str,str]:
 result:dict[str,str]={}
 for item in(style or "").split(";"):
  if ":" not in item:
   continue
  key,value=item.split(":",1)
  result[key.strip().lower()]=value.strip()
 return result
def format_style_attr(style_map:dict[str,str]|None)->str:
 if not style_map:
  return ""
 return ";".join(f"{key}:{value}" for key,value in sorted(style_map.items())if value)
def get_tooltip(element:Element)->str:
 for child in element:
  if strip_ns(child.tag)=="title":
   return(child.text or "").strip()
 return ""
def tooltip_value(element:Element)->str|None:
 tooltip=get_tooltip(element)
 if not tooltip:
  return None
 return " ".join(tooltip.split()).replace(";",",")
def tooltip_style(element:Element)->str:
 tooltip=tooltip_value(element)
 if tooltip is None:
  return ""
 return f"tooltip={tooltip};"
def link_value(url:str|None)->str|None:
 if not url:
  return None
 url=url.strip()
 if not url or _CONTROL_CHAR_RE.search(url):
  return None
 try:
  parsed=urlparse(url)
 except ValueError:
  return None
 if parsed.scheme and parsed.scheme.lower()not in _SAFE_LINK_SCHEMES:
  return None
 return quote(url,safe="/:#?&=%+,-._~[]@!$'()*")
def link_style(converter:Any)->str:
 url=getattr(converter,"link_url",getattr(converter,"_link_url",""))
 safe=link_value(url)
 if safe is None:
  return ""
 return f"link={safe};"
"""Affine transform helpers for SVG coordinate conversion."""
import math
import re
from xml.etree.ElementTree import Element
Matrix=list[float]
IDENTITY:Matrix=[1.0,0.0,0.0,1.0,0.0,0.0]
def mat_mul(left:Matrix,right:Matrix)->Matrix:
 return[left[0]*right[0]+left[2]*right[1],left[1]*right[0]+left[3]*right[1],left[0]*right[2]+left[2]*right[3],left[1]*right[2]+left[3]*right[3],left[0]*right[4]+left[2]*right[5]+left[4],left[1]*right[4]+left[3]*right[5]+left[5],]
def apply_pt(matrix:Matrix,x:float,y:float)->Point2D:
 return matrix[0]*x+matrix[2]*y+matrix[4],matrix[1]*x+matrix[3]*y+matrix[5]
def scale_x(matrix:Matrix)->float:
 return math.sqrt(matrix[0]**2+matrix[1]**2)
def scale_y(matrix:Matrix)->float:
 return math.sqrt(matrix[2]**2+matrix[3]**2)
def stroke_scale(matrix:Matrix)->float:
 return math.sqrt(scale_x(matrix)*scale_y(matrix))
def parse_transform(transform:str|None)->Matrix:
 if not transform:
  return IDENTITY[:]
 result=IDENTITY[:]
 for match in re.finditer(r"(\w+)\(([^)]+)\)",transform):
  function_name=match.group(1)
  numbers=[float(n)for n in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?",match.group(2))]
  if function_name=="translate":
   tx,ty=(numbers+[0.0,0.0])[:2]
   matrix=[1.0,0.0,0.0,1.0,tx,ty]
  elif function_name=="scale":
   sx=numbers[0]if numbers else 1.0
   sy=numbers[1]if len(numbers)>1 else sx
   matrix=[sx,0.0,0.0,sy,0.0,0.0]
  elif function_name=="rotate":
   angle=math.radians(numbers[0])if numbers else 0.0
   cx,cy=(numbers+[0.0,0.0,0.0])[1:3]
   cos_a,sin_a=math.cos(angle),math.sin(angle)
   matrix=[cos_a,sin_a,-sin_a,cos_a,cx-cx*cos_a+cy*sin_a,cy-cx*sin_a-cy*cos_a,]
  elif function_name=="skewX":
   angle=math.radians(numbers[0])if numbers else 0.0
   matrix=[1.0,0.0,math.tan(angle),1.0,0.0,0.0]
  elif function_name=="skewY":
   angle=math.radians(numbers[0])if numbers else 0.0
   matrix=[1.0,math.tan(angle),0.0,1.0,0.0,0.0]
  elif function_name=="matrix" and len(numbers)>=6:
   matrix=numbers[:6]
  else:
   continue
  result=mat_mul(result,matrix)
 return result
def viewbox_transform(svg_root:Element,override_w:float|None=None,override_h:float|None=None,)->Matrix:
 viewbox=svg_root.get("viewBox")
 if not viewbox:
  return IDENTITY[:]
 try:
  values=[float(value)for value in re.split(r"[\s,]+",viewbox.strip())if value]
 except ValueError:
  return IDENTITY[:]
 if len(values)<4:
  return IDENTITY[:]
 vb_x,vb_y,vb_w,vb_h=values
 if vb_w==0 or vb_h==0:
  return IDENTITY[:]
 if override_w is not None:
  width=override_w
 else:
  width_attr=svg_root.get("width",str(vb_w))
  width=vb_w if str(width_attr).strip().endswith("%")else(parse_length(width_attr)or vb_w)
 if override_h is not None:
  height=override_h
 else:
  height_attr=svg_root.get("height",str(vb_h))
  height=vb_h if str(height_attr).strip().endswith("%")else(parse_length(height_attr)or vb_h)
 scale_width=width/vb_w
 scale_height=height/vb_h
 preserve_aspect_ratio=(svg_root.get("preserveAspectRatio")or "xMidYMid meet").strip().lower()
 if "none" in preserve_aspect_ratio:
  return[scale_width,0.0,0.0,scale_height,-vb_x*scale_width,-vb_y*scale_height]
 scale=max(scale_width,scale_height)if "slice" in preserve_aspect_ratio else min(scale_width,scale_height)
 scaled_w=vb_w*scale
 scaled_h=vb_h*scale
 tx=-vb_x*scale+(width-scaled_w)/2
 if "xmin" in preserve_aspect_ratio:
  tx=-vb_x*scale
 elif "xmax" in preserve_aspect_ratio:
  tx=-vb_x*scale+(width-scaled_w)
 ty=-vb_y*scale+(height-scaled_h)/2
 if "ymin" in preserve_aspect_ratio:
  ty=-vb_y*scale
 elif "ymax" in preserve_aspect_ratio:
  ty=-vb_y*scale+(height-scaled_h)
 return[scale,0.0,0.0,scale,tx,ty]
"""Geometry helpers for converting transformed SVG elements into draw.io bounds."""
import math
from collections.abc import Sequence
from dataclasses import dataclass
@dataclass(frozen=True)
class BoundsBox:
 x:float
 y:float
 width:float
 height:float
 rotation:float|None=None
 def rotation_if_visible(self,threshold:float=0.01)->float|None:
  if self.rotation is None or abs(self.rotation)<=threshold:
   return None
  return self.rotation
 def has_distinct_axes(self,tolerance:float=0.02)->bool:
  return abs(self.width-self.height)>tolerance
def rotation_deg(matrix:Matrix)->float:
 return math.degrees(math.atan2(matrix[1],matrix[0]))
def has_shear(matrix:Matrix)->bool:
 return abs(matrix[0]*matrix[2]+matrix[1]*matrix[3])>1e-6
def line_endpoints(matrix:Matrix,x1:float,y1:float,x2:float,y2:float)->tuple[Point2D,Point2D]:
 return apply_pt(matrix,x1,y1),apply_pt(matrix,x2,y2)
def rect_corners(matrix:Matrix,x:float,y:float,width:float,height:float)->tuple[Point2D,...]:
 return(apply_pt(matrix,x,y),apply_pt(matrix,x+width,y),apply_pt(matrix,x+width,y+height),apply_pt(matrix,x,y+height),)
def bounds_from_points(points:Sequence[Point2D],rotation:float|None=None)->BoundsBox:
 xs=[point[0]for point in points]
 ys=[point[1]for point in points]
 x=min(xs)
 y=min(ys)
 width=max(xs)-x or 1.0
 height=max(ys)-y or 1.0
 return BoundsBox(x=x,y=y,width=width,height=height,rotation=rotation)
def ellipse_bounds(matrix:Matrix,cx:float,cy:float,rx:float,ry:float)->BoundsBox:
 tx,ty=apply_pt(matrix,cx,cy)
 radius_x=rx*scale_x(matrix)
 radius_y=ry*scale_y(matrix)
 return BoundsBox(x=tx-radius_x,y=ty-radius_y,width=radius_x*2,height=radius_y*2,rotation=rotation_deg(matrix),)
def rect_bounds(matrix:Matrix,x:float,y:float,width:float,height:float)->BoundsBox:
 tx,ty=apply_pt(matrix,x+width/2,y+height/2)
 scaled_width=width*scale_x(matrix)
 scaled_height=height*scale_y(matrix)
 return BoundsBox(x=tx-scaled_width/2,y=ty-scaled_height/2,width=scaled_width,height=scaled_height,rotation=rotation_deg(matrix),)
def image_bounds(matrix:Matrix,x:float,y:float,width:float,height:float)->BoundsBox:
 if has_shear(matrix):
  return bounds_from_points(rect_corners(matrix,x,y,width,height))
 return rect_bounds(matrix,x,y,width,height)
"""Arc-conversion helpers for SVG path processing."""
import math
def arc_to_bezier(x1:float,y1:float,rx:float,ry:float,phi_deg:float,large_arc:int,sweep:int,x2:float,y2:float,)->list[BezierCurve]:
 if x1==x2 and y1==y2:
  return[]
 if rx==0 or ry==0:
  return[(x1,y1,x2,y2,x2,y2)]
 phi=math.radians(phi_deg%360)
 cos_phi=math.cos(phi)
 sin_phi=math.sin(phi)
 dx,dy=(x1-x2)/2,(y1-y2)/2
 x1p=cos_phi*dx+sin_phi*dy
 y1p=-sin_phi*dx+cos_phi*dy
 rx,ry=abs(rx),abs(ry)
 x1p_sq,y1p_sq=x1p*x1p,y1p*y1p
 rx_sq,ry_sq=rx*rx,ry*ry
 lam=x1p_sq/rx_sq+y1p_sq/ry_sq
 if lam>1:
  lam=math.sqrt(lam)
  rx*=lam
  ry*=lam
  rx_sq=rx*rx
  ry_sq=ry*ry
 num=max(0.0,rx_sq*ry_sq-rx_sq*y1p_sq-ry_sq*x1p_sq)
 den=rx_sq*y1p_sq+ry_sq*x1p_sq
 sq=math.sqrt(num/den)if den else 0.0
 if large_arc==sweep:
  sq=-sq
 cxp=sq*rx*y1p/ry
 cyp=-sq*ry*x1p/rx
 cx=cos_phi*cxp-sin_phi*cyp+(x1+x2)/2
 cy=sin_phi*cxp+cos_phi*cyp+(y1+y2)/2
 def angle_between(ux:float,uy:float,vx:float,vy:float)->float:
  length=math.sqrt(ux*ux+uy*uy)*math.sqrt(vx*vx+vy*vy)
  if length==0:
   return 0.0
  cosine=max(-1.0,min(1.0,(ux*vx+uy*vy)/length))
  angle=math.acos(cosine)
  return-angle if ux*vy-uy*vx<0 else angle
 theta1=angle_between(1,0,(x1p-cxp)/rx,(y1p-cyp)/ry)
 dtheta=angle_between((x1p-cxp)/rx,(y1p-cyp)/ry,(-x1p-cxp)/rx,(-y1p-cyp)/ry,)
 if not sweep and dtheta>0:
  dtheta-=2*math.pi
 elif sweep and dtheta<0:
  dtheta+=2*math.pi
 segment_count=max(1,int(math.ceil(abs(dtheta)/(math.pi/2))))
 dt=dtheta/segment_count
 curves:list[BezierCurve]=[]
 theta=theta1
 for _ in range(segment_count):
  half=dt/2
  alpha=math.sin(dt)*(math.sqrt(4+3*math.tan(half)**2)-1)/3
  cos_theta,sin_theta=math.cos(theta),math.sin(theta)
  cos_next,sin_next=math.cos(theta+dt),math.sin(theta+dt)
  ex=cx+rx*cos_phi*cos_theta-ry*sin_phi*sin_theta
  ey=cy+rx*sin_phi*cos_theta+ry*cos_phi*sin_theta
  fx=cx+rx*cos_phi*cos_next-ry*sin_phi*sin_next
  fy=cy+rx*sin_phi*cos_next+ry*cos_phi*sin_next
  d1x=-rx*cos_phi*sin_theta-ry*sin_phi*cos_theta
  d1y=-rx*sin_phi*sin_theta+ry*cos_phi*cos_theta
  d2x=-rx*cos_phi*sin_next-ry*sin_phi*cos_next
  d2y=-rx*sin_phi*sin_next+ry*cos_phi*cos_next
  curves.append((ex+alpha*d1x,ey+alpha*d1y,fx-alpha*d2x,fy-alpha*d2y,fx,fy,))
  theta+=dt
 return curves
"""Bounding-box helpers for parsed SVG path commands."""
import math
from collections.abc import Iterator,Sequence
def _bezier_extremes_t(v0:float,v1:float,v2:float,v3:float)->Iterator[float]:
 a=v1-v0
 b=v2-v1
 c=v3-v2
 qa=a-2*b+c
 qb=2*(b-a)
 qc=a
 if abs(qa)<1e-10:
  if abs(qb)>1e-10:
   t=-qc/qb
   if 0.0<t<1.0:
    yield t
 else:
  disc=qb*qb-4*qa*qc
  if disc>=0:
   sq=math.sqrt(disc)
   for sign in(1,-1):
    t=(-qb+sign*sq)/(2*qa)
    if 0.0<t<1.0:
     yield t
def _bezier_eval(v0:float,v1:float,v2:float,v3:float,t:float)->float:
 return(1-t)**3*v0+3*(1-t)**2*t*v1+3*(1-t)*t**2*v2+t**3*v3
def _bezier_tight_pts(x0:float,y0:float,x1:float,y1:float,x2:float,y2:float,x3:float,y3:float,)->Iterator[Point2D]:
 yield x0,y0
 yield x3,y3
 ts:set[float]=set()
 for t in _bezier_extremes_t(x0,x1,x2,x3):
  ts.add(t)
 for t in _bezier_extremes_t(y0,y1,y2,y3):
  ts.add(t)
 for t in ts:
  yield _bezier_eval(x0,x1,x2,x3,t),_bezier_eval(y0,y1,y2,y3,t)
def commands_bbox(commands:Sequence[PathCommand])->tuple[float,float,float,float]|None:
 xs:list[float]=[]
 ys:list[float]=[]
 cur_x,cur_y=0.0,0.0
 for kind,points in commands:
  if kind=="move":
   cur_x,cur_y=points[0]
   xs.append(cur_x)
   ys.append(cur_y)
  elif kind=="line":
   cur_x,cur_y=points[0]
   xs.append(cur_x)
   ys.append(cur_y)
  elif kind=="curve":
   (x1,y1),(x2,y2),(x3,y3)=points
   for px,py in _bezier_tight_pts(cur_x,cur_y,x1,y1,x2,y2,x3,y3):
    xs.append(px)
    ys.append(py)
   cur_x,cur_y=x3,y3
 if not xs:
  return None
 bx=min(xs)
 by=min(ys)
 bw=max(xs)-bx
 bh=max(ys)-by
 if bw<=0:
  bw=1.0
 if bh<=0:
  bh=1.0
 return bx,by,bw,bh
"""SVG path tokenization and command parsing helpers."""
import re
import warnings
from collections.abc import Callable,Iterator
def tokenize_path(path_data:str|None)->list[str]:
 return re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?",path_data or "",)
_CMD_CHARS=frozenset("MmLlHhVvCcSsQqTtAaZz")
def _iter_svg_commands(path_data:str|None)->Iterator[tuple]:
 tokens=tokenize_path(path_data)
 i,cx,cy=0,0.0,0.0
 sx,sy=0.0,0.0
 last_cubic_cp:tuple[float,float]|None=None
 last_quad_cp:tuple[float,float]|None=None
 malformed=False
 while i<len(tokens):
  token=tokens[i]
  if token not in _CMD_CHARS:
   i+=1
   continue
  cmd,i=token,i+1
  if cmd in "Zz":
   yield("close",sx,sy)
   cx,cy=sx,sy
   last_cubic_cp=last_quad_cp=None
   continue
  while i<len(tokens)and tokens[i]not in _CMD_CHARS:
   try:
    if cmd in "Mm":
     nx,ny=float(tokens[i]),float(tokens[i+1])
     if cmd=="m":
      nx+=cx
      ny+=cy
     cx,cy=nx,ny
     sx,sy=cx,cy
     yield("move",cx,cy)
     cmd="L" if cmd=="M" else "l"
     last_cubic_cp=last_quad_cp=None
     i+=2
    elif cmd in "Ll":
     nx,ny=float(tokens[i]),float(tokens[i+1])
     if cmd=="l":
      nx+=cx
      ny+=cy
     cx,cy=nx,ny
     yield("line",cx,cy)
     last_cubic_cp=last_quad_cp=None
     i+=2
    elif cmd in "Hh":
     nx=float(tokens[i])
     if cmd=="h":
      nx+=cx
     cx=nx
     yield("line",cx,cy)
     last_cubic_cp=last_quad_cp=None
     i+=1
    elif cmd in "Vv":
     ny=float(tokens[i])
     if cmd=="v":
      ny+=cy
     cy=ny
     yield("line",cx,cy)
     last_cubic_cp=last_quad_cp=None
     i+=1
    elif cmd in "Cc":
     base_x=cx if cmd=="c" else 0.0
     base_y=cy if cmd=="c" else 0.0
     x1=base_x+float(tokens[i])
     y1=base_y+float(tokens[i+1])
     x2=base_x+float(tokens[i+2])
     y2=base_y+float(tokens[i+3])
     x3=base_x+float(tokens[i+4])
     y3=base_y+float(tokens[i+5])
     yield("cubic",x1,y1,x2,y2,x3,y3)
     last_cubic_cp=(x2,y2)
     last_quad_cp=None
     cx,cy=x3,y3
     i+=6
    elif cmd in "Ss":
     base_x=cx if cmd=="s" else 0.0
     base_y=cy if cmd=="s" else 0.0
     rx1=2*cx-last_cubic_cp[0]if last_cubic_cp is not None else cx
     ry1=2*cy-last_cubic_cp[1]if last_cubic_cp is not None else cy
     x2=base_x+float(tokens[i])
     y2=base_y+float(tokens[i+1])
     x3=base_x+float(tokens[i+2])
     y3=base_y+float(tokens[i+3])
     yield("cubic",rx1,ry1,x2,y2,x3,y3)
     last_cubic_cp=(x2,y2)
     last_quad_cp=None
     cx,cy=x3,y3
     i+=4
    elif cmd in "Qq":
     base_x=cx if cmd=="q" else 0.0
     base_y=cy if cmd=="q" else 0.0
     qx1=base_x+float(tokens[i])
     qy1=base_y+float(tokens[i+1])
     x3=base_x+float(tokens[i+2])
     y3=base_y+float(tokens[i+3])
     yield("cubic",cx+2/3*(qx1-cx),cy+2/3*(qy1-cy),x3+2/3*(qx1-x3),y3+2/3*(qy1-y3),x3,y3,)
     last_quad_cp=(qx1,qy1)
     last_cubic_cp=None
     cx,cy=x3,y3
     i+=4
    elif cmd in "Tt":
     base_x=cx if cmd=="t" else 0.0
     base_y=cy if cmd=="t" else 0.0
     qx1=2*cx-last_quad_cp[0]if last_quad_cp is not None else cx
     qy1=2*cy-last_quad_cp[1]if last_quad_cp is not None else cy
     x3=base_x+float(tokens[i])
     y3=base_y+float(tokens[i+1])
     yield("cubic",cx+2/3*(qx1-cx),cy+2/3*(qy1-cy),x3+2/3*(qx1-x3),y3+2/3*(qy1-y3),x3,y3,)
     last_quad_cp=(qx1,qy1)
     last_cubic_cp=None
     cx,cy=x3,y3
     i+=2
    elif cmd in "Aa" and i+6<len(tokens):
     rx_a=abs(float(tokens[i]))
     ry_a=abs(float(tokens[i+1]))
     phi_a=float(tokens[i+2])
     large_arc=int(float(tokens[i+3]))
     sweep=int(float(tokens[i+4]))
     if cmd=="A":
      nx,ny=float(tokens[i+5]),float(tokens[i+6])
     else:
      nx,ny=cx+float(tokens[i+5]),cy+float(tokens[i+6])
     for curve in arc_to_bezier(cx,cy,rx_a,ry_a,phi_a,large_arc,sweep,nx,ny):
      yield("cubic",curve[0],curve[1],curve[2],curve[3],curve[4],curve[5])
     last_cubic_cp=last_quad_cp=None
     cx,cy=nx,ny
     i+=7
    else:
     i+=1
   except(IndexError,ValueError):
    i+=1
    malformed=True
 if malformed:
  warnings.warn("SVG path data contained malformed or truncated token(s) that were skipped; " "the resulting geometry may be incomplete.",RuntimeWarning,stacklevel=2,)
def path_points(path_data:str|None)->Iterator[Point2D]:
 for cmd in _iter_svg_commands(path_data):
  if cmd[0]in("move","line"):
   yield cmd[1],cmd[2]
  elif cmd[0]=="cubic":
   yield cmd[1],cmd[2]
   yield cmd[3],cmd[4]
   yield cmd[5],cmd[6]
def sample_open_path(path_data:str|None)->Iterator[Point2D]:
 def cubic_mid(x0:float,y0:float,x1:float,y1:float,x2:float,y2:float,x3:float,y3:float,)->Point2D:
  return(0.125*x0+0.375*x1+0.375*x2+0.125*x3,0.125*y0+0.375*y1+0.375*y2+0.125*y3,)
 cx,cy=0.0,0.0
 for cmd in _iter_svg_commands(path_data):
  if cmd[0]=="move":
   cx,cy=cmd[1],cmd[2]
   yield cx,cy
  elif cmd[0]=="line":
   cx,cy=cmd[1],cmd[2]
   yield cx,cy
  elif cmd[0]=="cubic":
   x1,y1,x2,y2,x3,y3=cmd[1],cmd[2],cmd[3],cmd[4],cmd[5],cmd[6]
   yield cubic_mid(cx,cy,x1,y1,x2,y2,x3,y3)
   cx,cy=x3,y3
   yield cx,cy
  elif cmd[0]=="close":
   cx,cy=cmd[1],cmd[2]
def path_commands(path_data:str|None,point_transform:Callable[[float,float],Point2D]|None=None,)->list[PathCommand]:
 def tp(x:float,y:float)->Point2D:
  return point_transform(x,y)if point_transform else(x,y)
 commands:list[PathCommand]=[]
 for cmd in _iter_svg_commands(path_data):
  if cmd[0]=="move":
   commands.append(("move",(tp(cmd[1],cmd[2]),)))
  elif cmd[0]=="line":
   commands.append(("line",(tp(cmd[1],cmd[2]),)))
  elif cmd[0]=="cubic":
   commands.append(("curve",(tp(cmd[1],cmd[2]),tp(cmd[3],cmd[4]),tp(cmd[5],cmd[6])),))
  elif cmd[0]=="close":
   commands.append(("close",()))
 return commands
"""Ramer-Douglas-Peucker path simplification for draw.io stencil paths."""
import math
def _perp_dist(point:Point2D,start:Point2D,end:Point2D)->float:
 x0,y0=point
 x1,y1=start
 x2,y2=end
 dx,dy=x2-x1,y2-y1
 sq=dx*dx+dy*dy
 if sq==0.0:
  return math.hypot(x0-x1,y0-y1)
 t=max(0.0,min(1.0,((x0-x1)*dx+(y0-y1)*dy)/sq))
 return math.hypot(x0-(x1+t*dx),y0-(y1+t*dy))
def _rdp(points:list[Point2D],tolerance:float)->list[Point2D]:
 if len(points)<=2:
  return list(points)
 max_dist=0.0
 max_idx=0
 for i in range(1,len(points)-1):
  d=_perp_dist(points[i],points[0],points[-1])
  if d>max_dist:
   max_dist=d
   max_idx=i
 if max_dist<=tolerance:
  return[points[0],points[-1]]
 left=_rdp(points[:max_idx+1],tolerance)
 right=_rdp(points[max_idx:],tolerance)
 return left[:-1]+right
def simplify_path_commands(commands:list[PathCommand],tolerance:float)->list[PathCommand]:
 if tolerance<=0.0 or not commands:
  return commands
 result:list[PathCommand]=[]
 cur:Point2D=(0.0,0.0)
 line_run:list[Point2D]=[]
 def flush_run()->None:
  if not line_run:
   return
  simplified=_rdp(line_run,tolerance)if len(line_run)>=3 else line_run
  for pt in simplified[1:]:
   result.append(("line",(pt,)))
  line_run.clear()
 for kind,pts in commands:
  if kind=="move":
   flush_run()
   result.append((kind,pts))
   cur=pts[0]
  elif kind=="line":
   if not line_run:
    line_run.append(cur)
   line_run.append(pts[0])
   cur=pts[0]
  elif kind=="curve":
   flush_run()
   result.append((kind,pts))
   cur=pts[2]
  else:
   flush_run()
   result.append((kind,pts))
 flush_run()
 return result
"""Draw.io stencil serialization helpers for parsed SVG paths."""
import functools
import zlib
from base64 import b64encode
from collections.abc import Sequence
from urllib.parse import quote
_URI_SAFE="-_.!~*'()"
def commands_to_stencil_path(commands:Sequence[PathCommand],ox:float,oy:float,width:float,height:float,)->str:
 def norm_x(x:float)->float:
  return(x-ox)/width*100 if width else 0.0
 def norm_y(y:float)->float:
  return(y-oy)/height*100 if height else 0.0
 parts:list[str]=[]
 for kind,points in commands:
  if kind=="move":
   x,y=points[0]
   parts.append(f'<move x="{norm_x(x):.2f}" y="{norm_y(y):.2f}"/>')
  elif kind=="line":
   x,y=points[0]
   parts.append(f'<line x="{norm_x(x):.2f}" y="{norm_y(y):.2f}"/>')
  elif kind=="curve":
   (x1,y1),(x2,y2),(x3,y3)=points
   parts.append(f'<curve x1="{norm_x(x1):.2f}" y1="{norm_y(y1):.2f}"'f' x2="{norm_x(x2):.2f}" y2="{norm_y(y2):.2f}"'f' x3="{norm_x(x3):.2f}" y3="{norm_y(y3):.2f}"/>')
  elif kind=="close":
   parts.append("<close/>")
 return "".join(parts)
def path_to_stencil(path_data:str|None,ox:float,oy:float,width:float,height:float)->str:
 return commands_to_stencil_path(path_commands(path_data),ox,oy,width,height)
@functools.lru_cache(maxsize=256)
def _compress_drawio_text(text:str)->str:
 data=quote(text,safe=_URI_SAFE).encode("utf-8")
 compressor=zlib.compressobj(level=9,wbits=-15)
 compressed=compressor.compress(data)+compressor.flush()
 return b64encode(compressed).decode("ascii")
def make_stencil_style_from_xml(xml:str,fill:str|None,stroke:str|None,stroke_width:float,opacity:int,)->str|None:
 if not xml:
  return None
 from.model_styles import StyleBuilder
 encoded=_compress_drawio_text(xml)
 return(StyleBuilder().add("shape",f"stencil({encoded})").add("fillColor",fill).add("strokeColor",stroke).add("strokeWidth",stroke_width).add("opacity",opacity).build())
def make_stencil_style_from_commands(commands:Sequence[PathCommand],ox:float,oy:float,width:float,height:float,fill:str|None,stroke:str|None,stroke_width:float,opacity:int,fill_rule:str="nonzero",linecap:str="flat",linejoin:str="miter",)->str|None:
 tolerance=max(width,height)*0.002
 cmds=simplify_path_commands(list(commands),tolerance)
 stencil_path=commands_to_stencil_path(cmds,ox,oy,width,height)
 if not stencil_path:
  return None
 if fill_rule=="evenodd":
  path_elem=f'<path fillrule="evenodd">{stencil_path}</path>'
 else:
  path_elem=f"<path>{stencil_path}</path>"
 paint="stroke" if fill=="none" else "fillstroke"
 xml=(f'<shape w="100" h="100" aspect="variable" strokewidth="inherit"'f' strokelinecap="{linecap}" strokelinejoin="{linejoin}">'f"<background>{path_elem}<{paint}/></background></shape>")
 return make_stencil_style_from_xml(xml,fill,stroke,stroke_width,opacity)
def make_stencil_style(path_data:str|None,ox:float,oy:float,width:float,height:float,fill:str|None,stroke:str|None,stroke_width:float,opacity:int,fill_rule:str="nonzero",linecap:str="flat",linejoin:str="miter",)->str|None:
 return make_stencil_style_from_commands(path_commands(path_data),ox,oy,width,height,fill,stroke,stroke_width,opacity,fill_rule,linecap,linejoin,)
"""Compatibility facade for SVG path helpers.

The implementation now lives in smaller focused modules:
- `path_parser.py` for tokenization and command parsing
- `path_bounds.py` for tight bounding-box calculations
- `path_stencil.py` for draw.io stencil serialization
"""
"""Sutherland–Hodgman polygon clipping for gradient band approximation."""
_BEZIER_SAMPLES=16
def _bezier(p0:Point2D,p1:Point2D,p2:Point2D,p3:Point2D,t:float)->Point2D:
 mt=1.0-t
 return(mt**3*p0[0]+3.0*mt**2*t*p1[0]+3.0*mt*t**2*p2[0]+t**3*p3[0],mt**3*p0[1]+3.0*mt**2*t*p1[1]+3.0*mt*t**2*p2[1]+t**3*p3[1],)
def commands_to_polygon(commands:list[PathCommand])->list[Point2D]:
 subpaths:list[list[Point2D]]=[]
 cur:Point2D=(0.0,0.0)
 current:list[Point2D]=[]
 for kind,pts in commands:
  if kind=="move":
   if current:
    subpaths.append(current)
   current=[pts[0]]
   cur=pts[0]
  elif kind=="line":
   current.append(pts[0])
   cur=pts[0]
  elif kind=="curve":
   cp1,cp2,p3=pts
   for i in range(1,_BEZIER_SAMPLES+1):
    current.append(_bezier(cur,cp1,cp2,p3,i/_BEZIER_SAMPLES))
   cur=p3
  elif kind=="close":
   if current and current[-1]!=current[0]:
    current.append(current[0])
   subpaths.append(current)
   current=[]
 if current:
  subpaths.append(current)
 return max(subpaths,key=len)if subpaths else[]
def _inside(px:float,py:float,a:float,b:float,c:float)->bool:
 return a*px+b*py+c>=0.0
def _intersect(p1:Point2D,p2:Point2D,a:float,b:float,c:float)->Point2D:
 dx,dy=p2[0]-p1[0],p2[1]-p1[1]
 denom=a*dx+b*dy
 if abs(denom)<1e-9:
  return((p1[0]+p2[0])/2.0,(p1[1]+p2[1])/2.0)
 t=-(a*p1[0]+b*p1[1]+c)/denom
 return(p1[0]+t*dx,p1[1]+t*dy)
def _clip_halfplane(polygon:list[Point2D],a:float,b:float,c:float)->list[Point2D]:
 if not polygon:
  return[]
 result:list[Point2D]=[]
 n=len(polygon)
 for i in range(n):
  cur=polygon[i]
  nxt=polygon[(i+1)%n]
  cur_in=_inside(cur[0],cur[1],a,b,c)
  nxt_in=_inside(nxt[0],nxt[1],a,b,c)
  if cur_in:
   result.append(cur)
  if cur_in!=nxt_in:
   result.append(_intersect(cur,nxt,a,b,c))
 return result
def clip_polygon_strip(polygon:list[Point2D],lo:float,hi:float,*,vertical:bool,)->list[Point2D]:
 if vertical:
  clipped=_clip_halfplane(polygon,1.0,0.0,-lo)
  return _clip_halfplane(clipped,-1.0,0.0,hi)
 else:
  clipped=_clip_halfplane(polygon,0.0,1.0,-lo)
  return _clip_halfplane(clipped,0.0,-1.0,hi)
"""Text measurement helpers with a best-effort real font backend."""
import importlib
import os
from functools import lru_cache
from threading import Lock
from typing import Any
_TK_LOCK=Lock()
_QT_LOCK=Lock()
_tk:Any|None
_tkfont:Any|None
_qtgui:Any|None
_TK_ROOT:Any=None
_QT_APPLICATION:Any=None
_TK_AVAILABLE=True
_QT_AVAILABLE=True
_PIL_IMAGE_FONT:Any|None
_FONT_CACHE:dict[tuple[str,bool,bool],str|None]={}
def _optional_import(module_name:str)->Any|None:
 try:
  return importlib.import_module(module_name)
 except ImportError:
  return None
_tk=_optional_import("tkinter")
_tkfont=_optional_import("tkinter.font")
_qtgui=_optional_import("PySide6.QtGui")
_PIL_IMAGE_FONT=_optional_import("PIL.ImageFont")
def _normalized_family(family:str|None)->str:
 text=(family or "Helvetica").split(",",1)[0].strip()
 return text.strip("'\"")or "Helvetica"
def _weight_factor(character:str)->float:
 if character in "il.,'`!:;| ":
  return 0.34
 if character in "fjrt()[]{}":
  return 0.42
 if character in "mwMW@%#&":
  return 0.95
 if character.isupper():
  return 0.72
 if character.isdigit():
  return 0.61
 return 0.56
def _heuristic_metrics(text:str,font_size:float,*,bold:bool,italic:bool)->tuple[float,float]:
 lines=text.splitlines()or[text]
 width_factor=max(sum(_weight_factor(character)for character in line)or 1.0 for line in lines)
 width=max(width_factor*font_size,font_size*0.9)
 if bold:
  width*=1.04
 if italic:
  width*=1.02
 height=max(font_size*1.45*max(1,len(lines)),10.0)
 return width,height
def _get_tk_root()->Any|None:
 global _TK_ROOT,_TK_AVAILABLE
 if _tk is None or not _TK_AVAILABLE:
  return None
 if _TK_ROOT is not None:
  return _TK_ROOT
 with _TK_LOCK:
  if _TK_ROOT is None:
   try:
    root=_tk.Tk()
    root.withdraw()
   except Exception:
    _TK_AVAILABLE=False
    return None
   _TK_ROOT=root
  return _TK_ROOT
def _prepare_qt_offscreen_environment()->None:
 if os.name!="nt" and not os.environ.get("QT_QPA_PLATFORM"):
  if not os.environ.get("DISPLAY")and not os.environ.get("WAYLAND_DISPLAY"):
   os.environ["QT_QPA_PLATFORM"]="offscreen"
def _get_qt_application()->Any|None:
 global _QT_APPLICATION,_QT_AVAILABLE
 if _qtgui is None or not _QT_AVAILABLE:
  return None
 if _QT_APPLICATION is not None:
  return _QT_APPLICATION
 with _QT_LOCK:
  try:
   _prepare_qt_offscreen_environment()
   app=_qtgui.QGuiApplication.instance()
   if app is None:
    argv=["svg-to-drawio-text-metrics"]
    app=_qtgui.QGuiApplication(argv)
  except Exception:
   _QT_AVAILABLE=False
   return None
  _QT_APPLICATION=app
  return _QT_APPLICATION
def _font_dirs()->list[str]:
 candidates=[os.path.join(os.environ.get("WINDIR",""),"Fonts"),"/usr/share/fonts","/usr/local/share/fonts",os.path.expanduser("~/.fonts"),os.path.expanduser("~/.local/share/fonts"),"/System/Library/Fonts","/Library/Fonts",]
 return[directory for directory in candidates if directory and os.path.isdir(directory)]
def _candidate_font_names(family:str,*,bold:bool,italic:bool)->list[str]:
 normalized=_normalized_family(family).lower().replace(" ","")
 if normalized in{"helvetica","arial","sans-serif","sansserif"}:
  return["arialbi.ttf" if bold and italic else "","arialbd.ttf" if bold else "","ariali.ttf" if italic else "","arial.ttf","LiberationSans-BoldItalic.ttf" if bold and italic else "","LiberationSans-Bold.ttf" if bold else "","LiberationSans-Italic.ttf" if italic else "","LiberationSans-Regular.ttf","DejaVuSans-BoldOblique.ttf" if bold and italic else "","DejaVuSans-Bold.ttf" if bold else "","DejaVuSans-Oblique.ttf" if italic else "","DejaVuSans.ttf","NotoSans-BoldItalic.ttf" if bold and italic else "","NotoSans-Bold.ttf" if bold else "","NotoSans-Italic.ttf" if italic else "","NotoSans-Regular.ttf",]
 if normalized in{"times","timesnewroman","serif"}:
  return["timesbi.ttf" if bold and italic else "","timesbd.ttf" if bold else "","timesi.ttf" if italic else "","times.ttf","LiberationSerif-BoldItalic.ttf" if bold and italic else "","LiberationSerif-Bold.ttf" if bold else "","LiberationSerif-Italic.ttf" if italic else "","LiberationSerif-Regular.ttf","DejaVuSerif-BoldItalic.ttf" if bold and italic else "","DejaVuSerif-Bold.ttf" if bold else "","DejaVuSerif-Italic.ttf" if italic else "","DejaVuSerif.ttf","NotoSerif-BoldItalic.ttf" if bold and italic else "","NotoSerif-Bold.ttf" if bold else "","NotoSerif-Italic.ttf" if italic else "","NotoSerif-Regular.ttf",]
 if normalized in{"courier","couriernew","monospace"}:
  return["courbi.ttf" if bold and italic else "","courbd.ttf" if bold else "","couri.ttf" if italic else "","cour.ttf","LiberationMono-BoldItalic.ttf" if bold and italic else "","LiberationMono-Bold.ttf" if bold else "","LiberationMono-Italic.ttf" if italic else "","LiberationMono-Regular.ttf","DejaVuSansMono-BoldOblique.ttf" if bold and italic else "","DejaVuSansMono-Bold.ttf" if bold else "","DejaVuSansMono-Oblique.ttf" if italic else "","DejaVuSansMono.ttf","NotoSansMono-Bold.ttf" if bold else "","NotoSansMono-Regular.ttf",]
 return[normalized,f"{normalized}-bolditalic.ttf" if bold and italic else "",f"{normalized}-bold.ttf" if bold else "",f"{normalized}-italic.ttf" if italic else "",f"{normalized}.ttf",]
def _find_font_path(family:str,*,bold:bool,italic:bool)->str|None:
 cache_key=(_normalized_family(family).lower(),bold,italic)
 if cache_key in _FONT_CACHE:
  return _FONT_CACHE[cache_key]
 for directory in _font_dirs():
  for candidate_name in _candidate_font_names(family,bold=bold,italic=italic):
   if not candidate_name:
    continue
   for root,_,files in os.walk(directory):
    lower_to_name={name.lower():name for name in files}
    actual_name=lower_to_name.get(candidate_name.lower())
    if actual_name is not None:
     font_path=os.path.join(root,actual_name)
     _FONT_CACHE[cache_key]=font_path
     return font_path
 _FONT_CACHE[cache_key]=None
 return None
def _measure_with_pillow(text:str,font_size:float,font_family:str,*,bold:bool,italic:bool,)->tuple[float,float]|None:
 if _PIL_IMAGE_FONT is None:
  return None
 font_path=_find_font_path(font_family,bold=bold,italic=italic)
 if not font_path:
  return None
 try:
  font=_PIL_IMAGE_FONT.truetype(font_path,size=max(1,int(round(font_size))))
  lines=text.splitlines()or[text]
  widths:list[float]=[]
  heights:list[float]=[]
  for line in lines:
   sample=line or " "
   if hasattr(font,"getlength"):
    widths.append(float(font.getlength(sample)))
   else:
    left,_,right,_=font.getbbox(sample)
    widths.append(float(max(right-left,font_size*0.9)))
   _,top,_,bottom=font.getbbox(sample)
   heights.append(float(max(bottom-top,font_size*1.2)))
 except Exception:
  return None
 width=max(max(widths,default=0.0),font_size*0.9)
 height=max(sum(heights)or 0.0,font_size*1.2)
 return width,height
def _measure_with_qt(text:str,font_size:float,font_family:str,*,bold:bool,italic:bool,)->tuple[float,float]|None:
 app=_get_qt_application()
 if app is None or _qtgui is None:
  return None
 try:
  font=_qtgui.QFont(_normalized_family(font_family))
  font.setPixelSize(max(1,int(round(font_size))))
  font.setBold(bold)
  font.setItalic(italic)
  font.setKerning(True)
  option=_qtgui.QTextOption()
  option.setWrapMode(_qtgui.QTextOption.WrapMode.NoWrap)
  lines=text.splitlines()or[text]
  widths:list[float]=[]
  heights:list[float]=[]
  for line in lines:
   sample=line or " "
   layout=_qtgui.QTextLayout(sample,font)
   layout.setTextOption(option)
   layout.beginLayout()
   text_line=layout.createLine()
   if not text_line.isValid():
    layout.endLayout()
    continue
   text_line.setLineWidth(10_000_000.0)
   widths.append(float(text_line.naturalTextWidth()))
   heights.append(float(text_line.height()))
   layout.endLayout()
  if not widths or not heights:
   return None
 except Exception:
  return None
 width=max(max(widths,default=0.0),font_size*0.9)
 height=max(sum(heights)or 0.0,font_size*1.2)
 return width,height
@lru_cache(maxsize=4096)
def measure_text_detailed(text:str,font_size:float,font_family:str,font_weight:str="normal",font_style:str="normal",*,policy:str="auto",)->tuple[float,float,str]:
 normalized_text=text or " "
 normalized_family=_normalized_family(font_family)
 normalized_font_size=max(float(font_size),1.0)
 bold=font_weight in{"bold","600","700","800","900"}
 italic=font_style=="italic"
 if policy=="heuristic":
  width,height=_heuristic_metrics(normalized_text,normalized_font_size,bold=bold,italic=italic)
  return width,height,"heuristic"
 if policy in{"auto","system"}:
  qt_metrics=_measure_with_qt(normalized_text,normalized_font_size,normalized_family,bold=bold,italic=italic,)
  if qt_metrics is not None:
   return qt_metrics[0],qt_metrics[1],"qt"
  pillow_metrics=_measure_with_pillow(normalized_text,normalized_font_size,normalized_family,bold=bold,italic=italic,)
  if pillow_metrics is not None:
   return pillow_metrics[0],pillow_metrics[1],"pillow"
 root=_get_tk_root()
 if root is not None and _tkfont is not None:
  try:
   tk_font=_tkfont.Font(root=root,family=normalized_family,size=max(1,int(round(normalized_font_size))),weight="bold" if bold else "normal",slant="italic" if italic else "roman",)
   width=float(tk_font.measure(normalized_text))
   height=float(tk_font.metrics("linespace"))
   return max(width,normalized_font_size*0.9),max(height,normalized_font_size*1.2),"tk"
  except Exception:
   pass
 width,height=_heuristic_metrics(normalized_text,normalized_font_size,bold=bold,italic=italic)
 return width,height,"heuristic"
@lru_cache(maxsize=4096)
def measure_text(text:str,font_size:float,font_family:str,font_weight:str="normal",font_style:str="normal",*,policy:str="auto",)->tuple[float,float]:
 width,height,_=measure_text_detailed(text,font_size,font_family,font_weight=font_weight,font_style=font_style,policy=policy,)
 return width,height
"""Helpers for approximating SVG ``<textPath>`` content with editable glyph cells."""
import math
from collections.abc import Callable
from dataclasses import dataclass
@dataclass(frozen=True)
class PathPosition:
 x:float
 y:float
 angle_degrees:float
def normal_vector(angle_degrees:float)->Point2D:
 angle_radians=math.radians(angle_degrees)
 return(-math.sin(angle_radians),math.cos(angle_radians))
def _cubic_point(p0:Point2D,p1:Point2D,p2:Point2D,p3:Point2D,t:float,)->Point2D:
 inv_t=1.0-t
 return(inv_t**3*p0[0]+3.0*inv_t**2*t*p1[0]+3.0*inv_t*t**2*p2[0]+t**3*p3[0],inv_t**3*p0[1]+3.0*inv_t**2*t*p1[1]+3.0*inv_t*t**2*p2[1]+t**3*p3[1],)
def sample_path_polyline(path_data:str|None,*,point_transform:Callable[[float,float],Point2D]|None=None,curve_steps:int=20,)->list[Point2D]:
 commands=path_commands(path_data,point_transform=point_transform)
 points:list[Point2D]=[]
 current:Point2D|None=None
 subpath_start:Point2D|None=None
 for command,command_points in commands:
  if command=="move":
   current=command_points[0]
   subpath_start=current
   if not points or points[-1]!=current:
    points.append(current)
   continue
  if command=="line":
   current=command_points[0]
   if not points or points[-1]!=current:
    points.append(current)
   continue
  if command=="curve":
   if current is None:
    current=command_points[2]
    subpath_start=current
    if not points or points[-1]!=current:
     points.append(current)
    continue
   control_1,control_2,target=command_points
   for step in range(1,max(curve_steps,1)+1):
    curve_point=_cubic_point(current,control_1,control_2,target,step/max(curve_steps,1))
    if not points or points[-1]!=curve_point:
     points.append(curve_point)
   current=target
   continue
  if command=="close" and current is not None and subpath_start is not None and current!=subpath_start:
   points.append(subpath_start)
   current=subpath_start
 return points
def polyline_length(points:list[Point2D])->float:
 total=0.0
 for start,end in zip(points,points[1:],strict=False):
  total+=math.hypot(end[0]-start[0],end[1]-start[1])
 return total
def point_and_angle_at_distance(points:list[Point2D],distance:float)->PathPosition|None:
 if not points:
  return None
 if len(points)==1:
  return PathPosition(points[0][0],points[0][1],0.0)
 first_non_zero_segment:tuple[Point2D,Point2D]|None=None
 last_non_zero_segment:tuple[Point2D,Point2D]|None=None
 for start,end in zip(points,points[1:],strict=False):
  dx=end[0]-start[0]
  dy=end[1]-start[1]
  segment_length=math.hypot(dx,dy)
  if segment_length<=1e-9:
   continue
  if first_non_zero_segment is None:
   first_non_zero_segment=(start,end)
  last_non_zero_segment=(start,end)
  if distance<0.0:
   break
 if distance<0.0 and first_non_zero_segment is not None:
  start,end=first_non_zero_segment
  dx=end[0]-start[0]
  dy=end[1]-start[1]
  segment_length=math.hypot(dx,dy)
  ratio=distance/segment_length
  return PathPosition(start[0]+dx*ratio,start[1]+dy*ratio,math.degrees(math.atan2(dy,dx)),)
 remaining=distance
 for start,end in zip(points,points[1:],strict=False):
  dx=end[0]-start[0]
  dy=end[1]-start[1]
  segment_length=math.hypot(dx,dy)
  if segment_length<=1e-9:
   continue
  last_non_zero_segment=(start,end)
  if remaining<=segment_length:
   ratio=remaining/segment_length
   return PathPosition(start[0]+dx*ratio,start[1]+dy*ratio,math.degrees(math.atan2(dy,dx)),)
  remaining-=segment_length
 if last_non_zero_segment is None:
  return PathPosition(points[-1][0],points[-1][1],0.0)
 start,end=last_non_zero_segment
 dx=end[0]-start[0]
 dy=end[1]-start[1]
 segment_length=math.hypot(dx,dy)
 if segment_length<=1e-9:
  return PathPosition(end[0],end[1],0.0)
 ratio=remaining/segment_length
 return PathPosition(end[0]+dx*ratio,end[1]+dy*ratio,math.degrees(math.atan2(dy,dx)),)
def parse_start_offset(value:str|None,total_length:float)->float:
 text=(value or "").strip()
 if not text:
  return 0.0
 if text.endswith("%"):
  return total_length*parse_float(text[:-1],0.0)/100.0
 return parse_length(text,0.0)
