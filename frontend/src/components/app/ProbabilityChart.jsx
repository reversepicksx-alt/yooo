import React from 'react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';

export function ProbabilityChart({ data }) {
  if (!data || data.length === 0) return null;
  return (
    <div style={{ height: 140, width: '100%', marginTop: 12 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data}>
          <defs>
            <linearGradient id="colorProb" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#222238" vertical={false} />
          <XAxis dataKey="value" stroke="#555570" fontSize={9} tickLine={false} axisLine={false}
            tickFormatter={v => typeof v === 'number' ? v.toFixed(1) : v} />
          <YAxis hide />
          <Tooltip
            contentStyle={{ backgroundColor: '#141422', border: '1px solid #222238', borderRadius: 8, fontSize: 10, color: '#e8e8f0' }}
            itemStyle={{ color: '#10b981' }}
            labelStyle={{ color: '#8888a8' }}
          />
          <Area type="monotone" dataKey="probability" stroke="#10b981" fillOpacity={1} fill="url(#colorProb)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
