import { Route, Routes } from "react-router-dom";
import Layout from "@/components/Layout";
import Dashboard from "@/pages/Dashboard";
import LiveTrading from "@/pages/LiveTrading";
import DryTrading from "@/pages/DryTrading";
import Strategies from "@/pages/Strategies";
import Risk from "@/pages/Risk";
import Backtest from "@/pages/Backtest";
import Analytics from "@/pages/Analytics";
import Logs from "@/pages/Logs";
import Health from "@/pages/Health";
import Settings from "@/pages/Settings";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/live" element={<LiveTrading />} />
        <Route path="/dry" element={<DryTrading />} />
        <Route path="/strategies" element={<Strategies />} />
        <Route path="/risk" element={<Risk />} />
        <Route path="/backtest" element={<Backtest />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/health" element={<Health />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Layout>
  );
}
