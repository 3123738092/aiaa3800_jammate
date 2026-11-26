"""Offline GLB -> transparent PNG; no browser download or browser data involved.

Run with PYTHONPATH pointing to an isolated Mitsuba install, or install Mitsuba 3.9.1.
Uses the same original GLB, studio HDRI and camera as the interactive scene.
"""
from pathlib import Path
import json,struct
import numpy as np
import mitsuba as mi
mi.set_variant('scalar_rgb')
ROOT=Path(__file__).resolve().parents[2]
data=(ROOT/'web/assets/studio/jm-m1.glb').read_bytes()
size=struct.unpack_from('<I',data,12)[0]
gltf=json.loads(data[20:20+size])
binary=data[20+size+8:]
def attribute(index):
 a=gltf['accessors'][index];v=gltf['bufferViews'][a['bufferView']]
 dtype={5126:'<f4',5125:'<u4',5123:'<u2'}[a['componentType']]
 count=a['count']*{'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}[a['type']]
 return np.frombuffer(binary,dtype=dtype,count=count,offset=v.get('byteOffset',0)+a.get('byteOffset',0)).copy()
scene={'type':'scene','integrator':{'type':'path','max_depth':6,'hide_emitters':True},
 'sensor':{'type':'perspective','fov':31,'fov_axis':'y','to_world':mi.ScalarTransform4f().look_at(origin=[2.8,2.2,5.2],target=[0,1.12,0],up=[0,1,0]),'sampler':{'type':'independent','sample_count':128},'film':{'type':'hdrfilm','width':720,'height':630,'pixel_format':'rgba','filter':{'type':'tent'}}},
 'environment':{'type':'envmap','filename':str(ROOT/'web/assets/studio/studio_small_09_1k.hdr'),'scale':.8},
 'key':{'type':'rectangle','to_world':mi.ScalarTransform4f().look_at(origin=[-3,5,4],target=[0,1.2,0],up=[0,1,0]).scale([2,2,1]),'emitter':{'type':'area','radiance':{'type':'rgb','value':[12,11.7,11.2]}}},
}
for i,mesh in enumerate(gltf['meshes']):
 p=mesh['primitives'][0];positions=attribute(p['attributes']['POSITION']).reshape(-1,3);normals=attribute(p['attributes']['NORMAL']).reshape(-1,3);indices=attribute(p['indices']).astype(np.uint32)
 mat=gltf['materials'][p['material']].get('pbrMetallicRoughness',{})
 color=mat.get('baseColorFactor',[.5,.5,.5,1])[:3]
 roughness=mat.get('roughnessFactor',.4)
 if mat.get('metallicFactor',0)>.5:
  bsdf=mi.load_dict({'type':'roughconductor','material':'Al','alpha':max(.03,roughness**2),'specular_reflectance':{'type':'rgb','value':color}})
 else:bsdf=mi.load_dict({'type':'roughplastic','alpha':roughness**2,'diffuse_reflectance':{'type':'rgb','value':color}})
 props=mi.Properties();props['bsdf']=bsdf
 model=mi.Mesh('part-'+str(i),len(positions),len(indices)//3,props,has_vertex_normals=True)
 params=mi.traverse(model)
 # Interactive GLB uses a slight Y turn. Bake the same transform here.
 angle=-.28;c,s=np.cos(angle),np.sin(angle);rotation=np.array([[c,0,s],[0,1,0],[-s,0,c]])
 params['vertex_positions']=(positions@rotation.T).astype(np.float32).ravel();params['vertex_normals']=(normals@rotation.T).astype(np.float32).ravel();params['faces']=indices;params.update()
 scene['part'+str(i)]=model
import sys
target=ROOT/'web/assets/studio/jm-m1.png'
if '--mask-only' not in sys.argv:
 image=mi.render(mi.load_dict(scene),spp=128)
 mi.util.write_bitmap(str(target),image)
# A separate geometry coverage pass provides transparent camera background while
# retaining HDRI reflections in the beauty pass. No browser pixel data is used.
mask_scene=dict(scene);mask_scene.pop('environment');mask_scene.pop('key')
mask=mi.render(mi.load_dict(mask_scene),spp=16)
from PIL import Image
coverage=np.clip(np.asarray(mask)[...,3]*255,0,255).astype(np.uint8)
beauty=Image.open(target).convert('RGBA');beauty.putalpha(Image.fromarray(coverage));beauty.save(target)
print('Saved offline microphone PNG')
