import * as THREE from 'three';
import {mergeGeometries,mergeVertices} from 'three/addons/utils/BufferGeometryUtils.js';

// Original JM-M1 condenser microphone; dimensions in metres of a presentation model.
// Dense grille is real geometry, merged per material to keep draw calls small.
export function microphone() {
 const root=new THREE.Group();root.name='JM-M1';
 const metal=new THREE.MeshStandardMaterial({color:0xb8bab7,metalness:.92,roughness:.29});
 const satin=new THREE.MeshStandardMaterial({color:0x656866,metalness:.8,roughness:.38});
 const dark=new THREE.MeshStandardMaterial({color:0x191b1b,metalness:.32,roughness:.68});
 const rubber=new THREE.MeshStandardMaterial({color:0x222323,roughness:.9});
 const led=new THREE.MeshStandardMaterial({name:'record-led',color:0x9b5036,metalness:.3,roughness:.3,emissive:0x000000});
 const batches=new Map();
 function add(geometry,material,x=0,y=0,z=0,rotation=null){
  if(rotation)geometry.applyMatrix4(new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(...rotation)));
  geometry.translate(x,y,z);geometry=geometry.index?geometry.toNonIndexed():geometry;
  if(!batches.has(material))batches.set(material,[]);batches.get(material).push(geometry);
 }
 function cylinder(r,h,mat,y,x=0,z=0,rot){add(new THREE.CylinderGeometry(r,r,h,64),mat,x,y,z,rot);}
 function torus(r,t,mat,y){add(new THREE.TorusGeometry(r,t,6,64),mat,0,y,0,[Math.PI/2,0,0]);}
 function lathe(profile,mat){add(new THREE.LatheGeometry(profile.map(([r,y])=>new THREE.Vector2(r,y)),96),mat);}
 lathe([[0,.015],[.57,.015],[.625,.035],[.64,.065],[.635,.09],[.61,.12],[.52,.145],[.12,.16],[0,.16]],satin);
 cylinder(.6,.024,rubber,.018);cylinder(.105,.09,metal,.19);cylinder(.055,.43,metal,.43);
 cylinder(.073,.07,satin,.65);torus(.07,.008,metal,.67);
 const yoke=new THREE.CatmullRomCurve3([new THREE.Vector3(-.385,1.46,0),new THREE.Vector3(-.385,.89,0),new THREE.Vector3(-.32,.77,0),new THREE.Vector3(0,.72,0),new THREE.Vector3(.32,.77,0),new THREE.Vector3(.385,.89,0),new THREE.Vector3(.385,1.46,0)]);
 add(new THREE.TubeGeometry(yoke,64,.035,8,false),metal);
 cylinder(.09,.075,satin,1.43,-.37,0,[0,0,Math.PI/2]);cylinder(.09,.075,satin,1.43,.37,0,[0,0,Math.PI/2]);
 cylinder(.05,.082,metal,1.43,-.405,0,[0,0,Math.PI/2]);cylinder(.05,.082,metal,1.43,.405,0,[0,0,Math.PI/2]);
 lathe([[0,.99],[.17,.99],[.23,1.025],[.263,1.10],[.27,1.24],[.27,1.57],[.262,1.60],[0,1.60]],metal);
 torus(.267,.008,satin,1.10);torus(.269,.008,satin,1.54);
 cylinder(.273,.035,satin,1.59);cylinder(.275,.012,metal,1.61);
 // Capsule core and two crossed wire sets. No transparency noise or texture aliasing.
 const grillBottom=1.64,grillTop=2.27;
 function radius(y){return y<=2.0?.26:Math.sqrt(Math.max(.001,.27**2-(y-2.0)**2));}
 const profile=[[0,grillBottom],[.249,grillBottom],[.25,2.0]];
 for(let i=1;i<=16;i++){const y=2.0+i*.27/16;profile.push([Math.sqrt(Math.max(0,.25**2-(i*.25/16)**2)),y]);}
 profile.push([0,grillTop]);lathe(profile,dark);
 for(let y=grillBottom;y<2.26;y+=.025)torus(radius(y),.0037,metal,y);
 for(let i=0;i<40;i++){
  const angle=i/40*Math.PI*2,points=[];
  for(let j=0;j<=28;j++){const y=grillBottom+(grillTop-grillBottom)*j/28,r=radius(y);points.push(new THREE.Vector3(Math.cos(angle)*r,y,Math.sin(angle)*r));}
  add(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points),30,.0034,3,false),metal);
 }
 // Central spine, rear seam and a small recording indicator on the body.
 add(new THREE.BoxGeometry(.017,.57,.007),satin,0,1.94,.264);
 add(new THREE.BoxGeometry(.012,.32,.007),satin,0,1.36,-.27);
 add(new THREE.SphereGeometry(.018,16,8),led,0,1.34,.271);
 for(const [material,geometries]of batches){const merged=mergeVertices(mergeGeometries(geometries));merged.normalizeNormals();merged.computeBoundingSphere();const mesh=new THREE.Mesh(merged,material);mesh.name=material===led?'record-indicator':'microphone-part';root.add(mesh);geometries.forEach(g=>g.dispose());}
 return root;
}
