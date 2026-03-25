import React, { useState, useEffect } from 'react';
import { Trash2, Plus, ShieldAlert, Loader2 } from 'lucide-react';

export function AdminPanel({ user }: { user: any }) {
  const [grants, setGrants] = useState<any[]>([]);
  const [newEmail, setNewEmail] = useState('');
  const [newType, setNewType] = useState('Lifetime');
  const [loading, setLoading] = useState(true);

  const fetchGrants = async () => {
    try {
      const res = await fetch(`/api/admin/grants?email=${encodeURIComponent(user.email)}`, {
        headers: { 'x-session-token': user.sessionToken },
        cache: 'no-store'
      });
      if (res.ok) {
        setGrants(await res.json());
      } else {
        const data = await res.json();
        if (data.error === 'Database not connected') {
          alert('MongoDB is not connected! Any changes you make will be lost when the server restarts.\n\nPlease check your MONGO_URL in Environment Variables, and ensure you have whitelisted IP 0.0.0.0/0 in MongoDB Atlas.');
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchGrants();
  }, []);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newEmail) return;
    setLoading(true);
    await fetch(`/api/admin/grants?email=${encodeURIComponent(user.email)}`, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'x-session-token': user.sessionToken 
      },
      body: JSON.stringify({ targetEmail: newEmail, accessType: newType })
    });
    setNewEmail('');
    fetchGrants();
  };

  const handleRemove = async (targetEmail: string) => {
    if (!window.confirm(`Remove access for ${targetEmail}?`)) return;
    setLoading(true);
    await fetch(`/api/admin/grants/${encodeURIComponent(targetEmail)}?email=${encodeURIComponent(user.email)}`, {
      method: 'DELETE',
      headers: { 'x-session-token': user.sessionToken }
    });
    fetchGrants();
  };

  const groupedGrants = {
    Owner: grants.filter(g => g.access_type === 'Owner'),
    Lifetime: grants.filter(g => g.access_type === 'Lifetime'),
  };

  const renderTable = (title: string, data: any[], type: string, colorClass: string) => (
    <div className="mb-8">
      <h3 className={`text-lg font-bold mb-4 border-b border-zinc-800 pb-2 ${colorClass}`}>{title}</h3>
      {data.length === 0 ? (
        <p className="text-zinc-500 text-sm italic">No {type.toLowerCase()} members found.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500 text-sm">
                <th className="pb-3 font-medium">Email</th>
                <th className="pb-3 font-medium">Access Type</th>
                <th className="pb-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.map((grant, i) => (
                <tr key={i} className="border-b border-zinc-800/50 hover:bg-zinc-800/20 transition-colors">
                  <td className="py-4 text-zinc-300">{grant.email}</td>
                  <td className="py-4">
                    <span className={`px-2 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${
                      grant.access_type === 'Owner' ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20' :
                      'bg-purple-500/10 text-purple-400 border border-purple-500/20'
                    }`}>
                      {grant.access_type}
                    </span>
                  </td>
                  <td className="py-4 text-right">
                    {grant.access_type !== 'Owner' && (
                      <button
                        onClick={() => handleRemove(grant.email)}
                        className="p-2 text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors inline-flex"
                        title="Remove Access"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );

  return (
    <div className="max-w-4xl mx-auto space-y-6 p-6">
      <div className="bg-zinc-900/50 border border-zinc-800 rounded-2xl p-6 backdrop-blur-sm">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 bg-emerald-500/10 rounded-xl flex items-center justify-center border border-emerald-500/20">
            <ShieldAlert className="w-5 h-5 text-emerald-400" />
          </div>
          <div>
            <h2 className="text-xl font-black uppercase italic tracking-tight">Access Control</h2>
            <p className="text-sm text-zinc-400">Manage manual access grants for lifetime members.</p>
          </div>
        </div>

        <form onSubmit={handleAdd} className="flex flex-col sm:flex-row gap-3 mb-8">
          <input
            type="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            placeholder="Email address"
            className="flex-1 bg-black border border-zinc-800 rounded-xl px-4 py-2 text-white placeholder-zinc-600 focus:outline-none focus:border-emerald-500 transition-colors"
            required
          />
          <select
            value={newType}
            onChange={(e) => setNewType(e.target.value)}
            className="bg-black border border-zinc-800 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-emerald-500 transition-colors"
          >
            <option value="Lifetime">Lifetime</option>
          </select>
          <button
            type="submit"
            disabled={loading}
            className="bg-emerald-500 text-black font-bold px-6 py-2 rounded-xl hover:bg-emerald-400 transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
          >
            <Plus className="w-4 h-4" />
            Add Grant
          </button>
        </form>

        {loading && grants.length === 0 ? (
          <div className="flex justify-center py-12">
            <Loader2 className="w-8 h-8 text-emerald-500 animate-spin" />
          </div>
        ) : (
          <div>
            {renderTable('Owner', groupedGrants.Owner, 'Owner', 'text-amber-400')}
            {renderTable('Lifetime Members', groupedGrants.Lifetime, 'Lifetime', 'text-purple-400')}
          </div>
        )}
      </div>
    </div>
  );
}
