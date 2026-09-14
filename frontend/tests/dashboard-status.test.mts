import assert from "node:assert/strict";
import test from "node:test";
import { deriveDashboardStatus, preserveSuccessfulState } from "../services/dashboard-status.ts";

const health = {provider:"hybrid",mode:"LIVE",status:"OK",severity:"OK",system_status:"LIVE_PAPER",
  valid_symbols:30,failed_symbols:0,stale_symbols:0,errors:[]};

test("one helper endpoint failure is API degraded, not data error",()=>{
  assert.equal(deriveDashboardStatus({backendHealthOk:true,failedModules:["Strategy Health"],dataHealth:health}),"API DEGRADED");
});
test("partial provider data is data degraded",()=>{
  assert.equal(deriveDashboardStatus({backendHealthOk:true,failedModules:[],dataHealth:{...health,status:"PARTIAL",severity:"WARNING",system_status:"DATA_DEGRADED"}}),"DATA DEGRADED");
});
test("critical live data is data error",()=>{
  assert.equal(deriveDashboardStatus({backendHealthOk:true,failedModules:[],dataHealth:{...health,status:"DATA_ERROR",severity:"CRITICAL",system_status:"DATA_ERROR"}}),"DATA ERROR");
});
test("health failure is backend offline",()=>{
  assert.equal(deriveDashboardStatus({backendHealthOk:false,failedModules:["Portfolio"]}),"BACKEND OFFLINE");
});
test("off-hours false critical remains degraded",()=>{
  assert.equal(deriveDashboardStatus({backendHealthOk:true,failedModules:[],dataHealth:{...health,status:"DATA_ERROR",severity:"WARNING",system_status:"DATA_DEGRADED",analysis_mode:"ANALYSIS_ONLY"}}),"DATA DEGRADED");
});
test("healthy backend and modules are live paper",()=>{
  assert.equal(deriveDashboardStatus({backendHealthOk:true,failedModules:[],dataHealth:health}),"LIVE PAPER");
});
test("a failed module preserves its last successful state",()=>{
  const previous={portfolio_value:5123};
  assert.equal(preserveSuccessfulState(previous,{ok:false,label:"Portfolio"}),previous);
  assert.deepEqual(preserveSuccessfulState(previous,{ok:true,value:{portfolio_value:5200}}),{portfolio_value:5200});
});
