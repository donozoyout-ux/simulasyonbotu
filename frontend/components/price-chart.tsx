"use client";
import {useEffect,useRef,useState} from "react";
import {CandlestickSeries,ColorType,createChart,createSeriesMarkers,HistogramSeries,LineSeries,type SeriesMarker,type Time} from "lightweight-charts";
import type {Candle,MarketSnapshot,NewsItem} from "@/types";

type Overlay="ema20"|"ema50"|"ema200"|"bollinger"|"vwap";
type TradeMarker={timestamp:string;price:number;label?:string};

export function PriceChart({candles,levels,swings=[],entries=[],exits=[],news=[],snapshots=[]}:{candles:Candle[];levels?:{support?:number;resistance?:number;supportLow?:number;supportHigh?:number;resistanceLow?:number;resistanceHigh?:number;entry?:number;stop?:number;target?:number};swings?:Array<{timestamp:string;price:number;type:string}>;entries?:TradeMarker[];exits?:TradeMarker[];news?:NewsItem[];snapshots?:MarketSnapshot[]}){
  const ref=useRef<HTMLDivElement>(null);
  const [overlays,setOverlays]=useState<Set<Overlay>>(new Set(["ema20","ema50","bollinger","vwap"]));
  const [pane,setPane]=useState<"PRICE"|"RSI"|"MACD"|"VOLUME">("PRICE");
  const [selectedNews,setSelectedNews]=useState<NewsItem>();
  const [selectedSnapshot,setSelectedSnapshot]=useState<MarketSnapshot>();
  const toggle=(key:Overlay)=>setOverlays(current=>{const next=new Set(current);if(next.has(key))next.delete(key);else next.add(key);return next});
  useEffect(()=>{
    if(!ref.current||!candles.length)return;
    const chart=createChart(ref.current,{height:pane==="PRICE"?430:560,layout:{background:{type:ColorType.Solid,color:"#111821"},textColor:"#9aa7b7"},grid:{vertLines:{color:"#1c2631"},horzLines:{color:"#1c2631"}},rightPriceScale:{borderColor:"#26313d"},timeScale:{borderColor:"#26313d",timeVisible:true},handleScroll:true,handleScale:true});
    const time=(value:string)=>(new Date(value).getTime()/1000) as Time;
    const candle=chart.addSeries(CandlestickSeries,{upColor:"#14d99a",downColor:"#ef5b64",wickUpColor:"#14d99a",wickDownColor:"#ef5b64",borderVisible:false});
    candle.setData(candles.map(c=>({time:time(c.timestamp),open:c.open,high:c.high,low:c.low,close:c.close})));
    const addIndicator=(selector:(c:Candle)=>number|undefined,color:string,width:1|2=1)=>{const data=candles.map(c=>({time:time(c.timestamp),value:selector(c)})).filter((row):row is {time:Time;value:number}=>Number.isFinite(row.value));if(data.length){const line=chart.addSeries(LineSeries,{color,lineWidth:width,priceLineVisible:false,lastValueVisible:false});line.setData(data)}};
    if(overlays.has("ema20"))addIndicator(c=>c.indicators?.ema20,"#36a3ff",2);
    if(overlays.has("ema50"))addIndicator(c=>c.indicators?.ema50,"#f5ad45",2);
    if(overlays.has("ema200"))addIndicator(c=>c.indicators?.ema200,"#b786ff",2);
    if(overlays.has("vwap"))addIndicator(c=>c.indicators?.vwap,"#e7eef7",1);
    if(overlays.has("bollinger")){addIndicator(c=>c.indicators?.bollinger?.upper,"#66798f");addIndicator(c=>c.indicators?.bollinger?.middle,"#506176");addIndicator(c=>c.indicators?.bollinger?.lower,"#66798f")}
    const candleTimes=new Set(candles.map(item=>item.timestamp));
    const nearest=(stamp:string)=>candles.find(row=>new Date(row.timestamp).getTime()>=new Date(stamp).getTime());
    const markers:SeriesMarker<Time>[]=[...swings.filter(s=>candleTimes.has(s.timestamp)).map(s=>({time:time(s.timestamp),position:s.type==="SWING_HIGH"?"aboveBar" as const:"belowBar" as const,color:s.type==="SWING_HIGH"?"#f5ad45":"#36a3ff",shape:s.type==="SWING_HIGH"?"arrowDown" as const:"arrowUp" as const,text:s.type==="SWING_HIGH"?"SH":"SL"}))];
    for(const item of entries){const match=nearest(item.timestamp);if(match)markers.push({time:time(match.timestamp),position:"belowBar",color:"#14d99a",shape:"arrowUp",text:item.label||`BUY ${item.price.toFixed(2)}`})}
    for(const item of exits){const match=nearest(item.timestamp);if(match)markers.push({time:time(match.timestamp),position:"aboveBar",color:item.label==="STOP"?"#ef5b64":"#b786ff",shape:"circle",text:item.label||`EXIT ${item.price.toFixed(2)}`})}
    for(const item of news){const match=nearest(item.published_at);if(match)markers.push({id:`news:${item.id??item.source_id}`,time:time(match.timestamp),position:"aboveBar",color:item.source==="KAP"?"#f5ad45":"#36a3ff",shape:"square",text:`${item.source} ${item.ai_importance??""}`.trim()})}
    for(const item of snapshots){const match=nearest(item.timestamp);if(match)markers.push({id:`snapshot:${item.id??item.timestamp}`,time:time(match.timestamp),position:"belowBar",color:"#b786ff",shape:"circle",text:`S ${item.technical_score??"—"}`})}
    createSeriesMarkers(candle,markers.sort((a,b)=>Number(a.time)-Number(b.time)));
    chart.subscribeClick(param=>{const id=String(param.hoveredObjectId||"");if(id.startsWith("news:")){const key=id.slice(5);setSelectedNews(news.find(item=>String(item.id??item.source_id)===key));setSelectedSnapshot(undefined)}else if(id.startsWith("snapshot:")){const key=id.slice(9);setSelectedSnapshot(snapshots.find(item=>String(item.id??item.timestamp)===key));setSelectedNews(undefined)}});
    const levelLines:[number|undefined,string][]=[[levels?.supportLow||levels?.support,"#36a3ff"],[levels?.supportHigh,"#36a3ff"],[levels?.resistanceLow||levels?.resistance,"#f5ad45"],[levels?.resistanceHigh,"#f5ad45"],[levels?.entry,"#14d99a"],[levels?.stop,"#ef5b64"],[levels?.target,"#b786ff"]];
    levelLines.forEach(([value,color])=>{if(value){const line=chart.addSeries(LineSeries,{color,lineWidth:1,lineStyle:2,priceLineVisible:false,lastValueVisible:true});line.setData(candles.map(c=>({time:time(c.timestamp),value})))}});
    if(pane==="RSI"){const rsi=chart.addSeries(LineSeries,{color:"#b786ff",lineWidth:2,priceLineVisible:false,lastValueVisible:true},1);rsi.setData(candles.filter(c=>Number.isFinite(c.indicators?.rsi)).map(c=>({time:time(c.timestamp),value:c.indicators!.rsi!})));[30,50,70].forEach(value=>{const line=chart.addSeries(LineSeries,{color:value===50?"#66798f":"#3f4c5a",lineWidth:1,lineStyle:2,priceLineVisible:false,lastValueVisible:false},1);line.setData(candles.map(c=>({time:time(c.timestamp),value})))})}
    if(pane==="MACD"){const addMacd=(key:"macd"|"signal",color:string)=>{const data=candles.filter(c=>c.indicators?.macd).map(c=>({time:time(c.timestamp),value:c.indicators!.macd![key]}));const line=chart.addSeries(LineSeries,{color,lineWidth:2,priceLineVisible:false},1);line.setData(data)};addMacd("macd","#36a3ff");addMacd("signal","#f5ad45");const hist=chart.addSeries(HistogramSeries,{priceLineVisible:false},1);hist.setData(candles.filter(c=>c.indicators?.macd).map(c=>({time:time(c.timestamp),value:c.indicators!.macd!.histogram,color:c.indicators!.macd!.histogram>=0?"#14d99a88":"#ef5b6488"})))}
    if(pane==="VOLUME"){const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:"volume"},priceLineVisible:false},1);volume.setData(candles.map(c=>({time:time(c.timestamp),value:c.volume,color:c.close>=c.open?"#14d99a88":"#ef5b6488"})));const average=chart.addSeries(LineSeries,{color:"#f5ad45",lineWidth:2,priceLineVisible:false},1);average.setData(candles.filter(c=>c.indicators?.volume_sma20).map(c=>({time:time(c.timestamp),value:c.indicators!.volume_sma20!})))}
    chart.timeScale().fitContent();const ro=new ResizeObserver(()=>chart.applyOptions({width:ref.current?.clientWidth||800}));ro.observe(ref.current);return()=>{ro.disconnect();chart.remove()};
  },[candles,levels,swings,entries,exits,news,snapshots,overlays,pane]);
  return <div className="pro-chart"><div className="chart-tools"><div>{(["ema20","ema50","ema200","bollinger","vwap"] as Overlay[]).map(key=><button key={key} className={overlays.has(key)?"active":""} onClick={()=>toggle(key)}>{key.toUpperCase()}</button>)}</div><div>{(["PRICE","RSI","MACD","VOLUME"] as const).map(key=><button key={key} className={pane===key?"active":""} onClick={()=>setPane(key)}>{key}</button>)}</div></div><div ref={ref} className="chart" aria-label="İndikatörlü mum grafiği"/>{selectedSnapshot&&<aside className="historical-state"><b>SNAPSHOT • {new Date(selectedSnapshot.timestamp).toLocaleString("tr-TR")}</b><span>Score {selectedSnapshot.technical_score??"—"} • {selectedSnapshot.trend||"—"} • {selectedSnapshot.market_structure||"—"}</span><small>{selectedSnapshot.analysis_mode||selectedSnapshot.status}</small></aside>}{selectedNews&&<aside className="historical-state"><b>{selectedNews.source} • {new Date(selectedNews.published_at).toLocaleString("tr-TR")}</b><span>{selectedNews.title} • {selectedNews.category} • {selectedNews.ai_sentiment||"BEKLIYOR"} • önem {selectedNews.ai_importance??"—"}</span><small>{selectedNews.ai_summary||"AI özeti yok"}</small></aside>}</div>;
}
