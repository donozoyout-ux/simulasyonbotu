"use client";
import {useEffect,useRef} from "react";
import {CandlestickSeries,ColorType,createChart,createSeriesMarkers,LineSeries, type SeriesMarker,type Time} from "lightweight-charts";
import type {Candle} from "@/types";

export function PriceChart({candles,levels,swings=[],exits=[]}:{candles:Candle[];levels?:{support:number;resistance:number;entry?:number;stop?:number;target?:number};swings?:Array<{timestamp:string;price:number;type:string}>;exits?:Array<{timestamp:string;price:number}>}){
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!ref.current || !candles.length)return;
    const chart=createChart(ref.current,{height:390,layout:{background:{type:ColorType.Solid,color:"#111821"},textColor:"#8793a3"},grid:{vertLines:{color:"#1c2631"},horzLines:{color:"#1c2631"}},rightPriceScale:{borderColor:"#26313d"},timeScale:{borderColor:"#26313d",timeVisible:true}});
    const candle=chart.addSeries(CandlestickSeries,{upColor:"#14d99a",downColor:"#ef5b64",wickUpColor:"#14d99a",wickDownColor:"#ef5b64",borderVisible:false});
    candle.setData(candles.map(c=>({time:(new Date(c.timestamp).getTime()/1000) as Time,open:c.open,high:c.high,low:c.low,close:c.close})));
    const candleTimes=new Set(candles.map(item=>item.timestamp));
    const markers:SeriesMarker<Time>[]=[...swings.filter(s=>candleTimes.has(s.timestamp)).map(s=>({time:(new Date(s.timestamp).getTime()/1000) as Time,position:s.type==="SWING_HIGH"?"aboveBar" as const:"belowBar" as const,color:s.type==="SWING_HIGH"?"#f5ad45":"#36a3ff",shape:s.type==="SWING_HIGH"?"arrowDown" as const:"arrowUp" as const,text:s.type==="SWING_HIGH"?"SH":"SL"}))];
    for(const item of exits){const exitMs=new Date(item.timestamp).getTime();const match=candles.find(row=>new Date(row.timestamp).getTime()>=exitMs);if(match)markers.push({time:(new Date(match.timestamp).getTime()/1000) as Time,position:"aboveBar",color:"#b786ff",shape:"circle",text:`EXIT ${item.price.toFixed(2)}`})}
    createSeriesMarkers(candle,markers);
    const colors:{key:keyof NonNullable<typeof levels>;color:string}[]=[{key:"support",color:"#36a3ff"},{key:"resistance",color:"#f5ad45"},{key:"entry",color:"#14d99a"},{key:"stop",color:"#ef5b64"},{key:"target",color:"#b786ff"}];
    colors.forEach(({key,color})=>{const value=levels?.[key];if(value){const line=chart.addSeries(LineSeries,{color,lineWidth:1,lineStyle:2,priceLineVisible:false,lastValueVisible:true});line.setData(candles.map(c=>({time:(new Date(c.timestamp).getTime()/1000) as Time,value})));}});
    chart.timeScale().fitContent();
    const ro=new ResizeObserver(()=>chart.applyOptions({width:ref.current?.clientWidth||800}));ro.observe(ref.current);
    return()=>{ro.disconnect();chart.remove()};
  },[candles,levels,swings,exits]);
  return <div ref={ref} className="chart" aria-label="Mum grafiği"/>;
}
