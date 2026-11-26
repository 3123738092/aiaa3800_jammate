import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {HDRLoader} from 'three/addons/loaders/HDRLoader.js';

// This module owns GPU resources only. It does not request microphone or MIDI access,
// create audio contexts, submit commands, or know anything about backend schemas.
export async function createMicrophoneScene(host,{onFailure=()=>{}}={}) {
 const timing={},start=performance.now();
 const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,powerPreference:'low-power',preserveDrawingBuffer:true});
 renderer.setPixelRatio(Math.min(devicePixelRatio,innerWidth<600?1:1.5));
 renderer.setClearColor(0x000000,0);renderer.outputColorSpace=THREE.SRGBColorSpace;
 renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.12;
 renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFShadowMap;
 renderer.domElement.setAttribute('aria-hidden','true');host.append(renderer.domElement);
 const scene=new THREE.Scene();
 const camera=new THREE.PerspectiveCamera(31,1,.1,40);camera.position.set(2.8,2.2,5.2);camera.lookAt(0,1.12,0);
 const pmrem=new THREE.PMREMGenerator(renderer);pmrem.compileEquirectangularShader();
 let model,environment;
 try {
  await Promise.all([
   new GLTFLoader().loadAsync('/assets/studio/jm-m1.glb').then(g=>{model=g.scene;timing.glbMs=performance.now()-start;}),
   new HDRLoader().loadAsync('/assets/studio/studio_small_09_1k.hdr').then(hdr=>{environment=pmrem.fromEquirectangular(hdr);scene.environment=environment.texture;hdr.dispose();timing.hdriMs=performance.now()-start;}),
  ]);
 } catch(error){renderer.dispose();renderer.domElement.remove();environment?.dispose();pmrem.dispose();throw error;}
 scene.environmentIntensity=.85;pmrem.dispose();
 model.rotation.y=-.28;scene.add(model);
 model.traverse(node=>{if(node.isMesh){node.castShadow=true;node.receiveShadow=true;}});
 const key=new THREE.DirectionalLight(0xfff8eb,3.3);key.position.set(-3,5,4);key.castShadow=true;key.shadow.mapSize.set(1024,1024);key.shadow.camera.left=-2;key.shadow.camera.right=2;key.shadow.camera.top=3;key.shadow.camera.bottom=-2;key.shadow.bias=-.0004;key.shadow.normalBias=.025;key.shadow.radius=4;scene.add(key);
 const fill=new THREE.DirectionalLight(0xf1f4ff,1);fill.position.set(4,3,-1);scene.add(fill);
 const floor=new THREE.Mesh(new THREE.PlaneGeometry(200,200),new THREE.ShadowMaterial({opacity:.035}));floor.rotation.x=-Math.PI/2;floor.position.y=.001;floor.receiveShadow=true;scene.add(floor);
 const shadowCanvas=document.createElement('canvas');shadowCanvas.width=128;shadowCanvas.height=128;
 const ctx=shadowCanvas.getContext('2d'),gradient=ctx.createRadialGradient(64,64,4,64,64,64);gradient.addColorStop(0,'rgba(20,22,20,.24)');gradient.addColorStop(.45,'rgba(20,22,20,.12)');gradient.addColorStop(1,'rgba(20,22,20,0)');ctx.fillStyle=gradient;ctx.fillRect(0,0,128,128);
 const contactMap=new THREE.CanvasTexture(shadowCanvas),contact=new THREE.Mesh(new THREE.PlaneGeometry(2.4,2.0),new THREE.MeshBasicMaterial({map:contactMap,transparent:true,depthWrite:false}));contact.rotation.x=-Math.PI/2;contact.position.y=.006;scene.add(contact);
 const led=[];model.traverse(n=>{if(n.isMesh&&n.material.name==='record-led')led.push(n.material);});
 let visible=true,active=true,disposed=false,raf=0,yaw=0,pitch=0,targetYaw=0,targetPitch=0,renderedFrames=0;
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 function render(){if(!disposed&&visible&&active&&!document.hidden){renderer.render(scene,camera);renderedFrames++;}}
 function frame(){raf=0;if(disposed||!visible||!active||document.hidden)return;const k=reduced.matches?1:.16;yaw+=(targetYaw-yaw)*k;pitch+=(targetPitch-pitch)*k;model.rotation.y=-.28+yaw;model.rotation.x=pitch;render();if(Math.abs(yaw-targetYaw)+Math.abs(pitch-targetPitch)>.0008)raf=requestAnimationFrame(frame);}
 function invalidate(){if(!raf&&!disposed)raf=requestAnimationFrame(frame);}
 function resize(){const r=host.getBoundingClientRect();if(r.width<1||r.height<1)return;renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();invalidate();}
 const ro=new ResizeObserver(resize);ro.observe(host);resize();
 const io=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible)invalidate();});io.observe(host);
 const visibility=()=>{if(document.hidden){cancelAnimationFrame(raf);raf=0;}else invalidate();};document.addEventListener('visibilitychange',visibility);
 const lost=e=>{e.preventDefault();active=false;cancelAnimationFrame(raf);onFailure('图形设备已重置，已切换静态画面');};renderer.domElement.addEventListener('webglcontextlost',lost);
 await renderer.compileAsync(scene,camera);render();timing.readyMs=performance.now()-start;
 return {
  timing,
  setActive(value){active=value;renderer.domElement.hidden=!value;if(value)invalidate();else{cancelAnimationFrame(raf);raf=0;}},
  pose(x=0,y=0){targetYaw=reduced.matches?0:x*.18;targetPitch=reduced.matches?0:y*.06;invalidate();},
  record(value){for(const material of led){material.color.set(value?0xd9552c:0x9b5036);material.emissive.set(value?0x8d250a:0x000000);}invalidate();},
  stress(t){if(disposed||!active||document.hidden)return;model.rotation.y=-.28+Math.sin(t*.001)*.15;renderer.render(scene,camera);renderedFrames++;},
  stats(){return{calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,textures:renderer.info.memory.textures,geometries:renderer.info.memory.geometries,renderedFrames};},
  snapshot(){const size=renderer.getSize(new THREE.Vector2()),ratio=renderer.getPixelRatio();renderer.setPixelRatio(1);renderer.setSize(960,840,false);renderer.render(scene,camera);const url=renderer.domElement.toDataURL('image/png');renderer.setPixelRatio(ratio);renderer.setSize(size.x,size.y,false);invalidate();return url;},
  dispose(){disposed=true;cancelAnimationFrame(raf);ro.disconnect();io.disconnect();document.removeEventListener('visibilitychange',visibility);renderer.domElement.removeEventListener('webglcontextlost',lost);scene.traverse(n=>{n.geometry?.dispose();if(n.material)for(const m of Array.isArray(n.material)?n.material:[n.material])m.dispose();});contactMap.dispose();environment.dispose();renderer.dispose();renderer.domElement.remove();},
 };
}
