import React, {useEffect, useMemo, useState} from "react";
import {createRoot} from "react-dom/client";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./styles.css";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const services=[
  {id:"electricity",label:"Electricity",icon:"⚡"},
  {id:"water",label:"Water",icon:"💧"},
  {id:"internet",label:"Internet",icon:"🌐"},
  {id:"mobile",label:"Mobile",icon:"📱"}
];

function App(){
  const [reports,setReports]=useState([]);
  const [service,setService]=useState("all");
  const [map,setMap]=useState(null);
  const [form,setForm]=useState({service:"electricity",description:""});
  const [loc,setLoc]=useState(null);
  const visible=useMemo(()=>service==="all"?reports:reports.filter(r=>r.service===service),[reports,service]);

  useEffect(()=>{
    fetch(API+"/api/reports").then(r=>r.json()).then(setReports).catch(()=>{});
    navigator.geolocation?.getCurrentPosition(p=>setLoc([p.coords.latitude,p.coords.longitude]));
    const ws=new WebSocket(API.replace(/^http/,"ws")+"/ws");
    ws.onmessage=e=>{const m=JSON.parse(e.data); if(m.type==="snapshot")setReports(m.reports); if(m.type==="report.created")setReports(x=>[...x.filter(r=>r.id!==m.report.id),m.report]); if(m.type==="report.updated")setReports(x=>x.map(r=>r.id===m.report.id?m.report:r));};
    return()=>ws.close();
  },[]);

  useEffect(()=>{
    const m=L.map("map",{zoomControl:false}).setView([20.5937,78.9629],5);
    L.control.zoom({position:"bottomright"}).addTo(m);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{attribution:"© OpenStreetMap contributors"}).addTo(m);
    setMap(m); return()=>m.remove();
  },[]);

  useEffect(()=>{
    if(!map)return;
    map.eachLayer(l=>{if(l instanceof L.CircleMarker)map.removeLayer(l)});
    visible.forEach(r=>{
      const color=r.status==="restored"?"#16a34a":r.status==="partial"?"#f59e0b":"#dc2626";
      const marker=L.circleMarker([r.lat,r.lng],{radius:9,fillColor:color,color:"#fff",weight:2,fillOpacity:.9});
      marker.bindPopup("<b>"+r.service.toUpperCase()+"</b><br>"+r.status+"<br>Confidence: "+r.confidence+"%<br>"+(r.description||"No description"));
      marker.addTo(map);
    });
    if(loc){L.circleMarker(loc,{radius:7,color:"#2563eb",fillColor:"#2563eb",fillOpacity:1}).bindPopup("Your location").addTo(map)}
  },[map,visible,loc]);

  async function report(e){
    e.preventDefault();
    const position=loc;
    if(!position){alert("Allow location access to submit a report.");return}
    const res=await fetch(API+"/api/reports",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({service:form.service,status:"down",lat:position[0],lng:position[1],description:form.description})});
    if(res.ok){setForm(x=>({...x,description:""}));alert("Report added. Nearby users can now see it.");}
  }

  return <div className="app">
    <header><div><strong>LiveCity</strong><span>See what's working. See what's not.</span></div><button onClick={()=>loc&&map?.setView(loc,14)}>📍 My location</button></header>
    <aside>
      <h1>Live essential services</h1><p className="muted">Community-powered outage visibility.</p>
      <div className="chips"><button className={service==="all"?"active":""} onClick={()=>setService("all")}>All</button>{services.map(s=><button key={s.id} className={service===s.id?"active":""} onClick={()=>setService(s.id)}>{s.icon} {s.label}</button>)}</div>
      <section className="card"><h2>Report a problem</h2><form onSubmit={report}><select value={form.service} onChange={e=>setForm({...form,service:e.target.value})}>{services.map(s=><option key={s.id} value={s.id}>{s.label}</option>)}</select><textarea value={form.description} onChange={e=>setForm({...form,description:e.target.value})} placeholder="What is happening?"/><button className="primary">Report outage</button></form></section>
      <section className="stats"><div><b>{visible.filter(r=>r.status!=="restored").length}</b><span>Active reports</span></div><div><b>{visible.filter(r=>r.confidence>=80).length}</b><span>High confidence</span></div></section>
      <p className="foot">Reports are community signals, not official utility confirmations.</p>
    </aside>
    <main id="map"/>
  </div>
}
createRoot(document.getElementById("root")).render(<App/>);
