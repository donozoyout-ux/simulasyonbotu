import assert from "node:assert/strict";
import test from "node:test";
import { DASHBOARD_TABS } from "../services/dashboard-navigation.ts";

test("dashboard exposes exactly five regrouped navigation tabs",()=>{
  assert.deepEqual([...DASHBOARD_TABS],["Ana Sayfa","Tarayıcı","Grafik & Analiz","Piyasa Hafızası","Sistem"]);
  assert.equal(new Set(DASHBOARD_TABS).size,5);
});
