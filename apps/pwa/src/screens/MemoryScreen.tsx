import type { Bootstrap } from "../api/client";
import { activeFacts, normalizeFacts, ORIGIN_LABELS, proposals } from "../state/memory";

export function MemoryScreen({ bootstrap }: { bootstrap: Bootstrap }) {
  const facts = normalizeFacts(bootstrap.profile ?? []);
  const waiting = proposals(facts);
  const active = activeFacts(facts);

  return (
    <div>
      <h1>Memori</h1>
      <h2>Profil</h2>
      {active.length === 0 ? (
        <p className="muted">Belum ada fakta profil. Puspa belajar dari percakapanmu.</p>
      ) : (
        active.map((f, i) => (
          <div key={`${f.key}-${i}`} className="card">
            <strong>{f.key}</strong>
            {f.value ? <span>: {f.value}</span> : null}
            <p className="muted">
              {ORIGIN_LABELS[f.origin]}
              {f.status !== "unknown" ? ` · ${f.status}` : ""}
            </p>
          </div>
        ))
      )}

      <h2>Usulan</h2>
      <p className="muted">
        Fakta yang disimpulkan agen menunggu konfirmasimu di sini. Konfirmasi dan koreksi Usulan
        berjalan lewat chat sampai endpoint kelola memori mendarat di API.
      </p>
      {waiting.length === 0 ? (
        <p className="muted">Tidak ada Usulan menunggu.</p>
      ) : (
        waiting.map((f, i) => (
          <div key={`${f.key}-${i}`} className="card">
            <strong>{f.key}</strong>
            {f.value ? <span>: {f.value}</span> : null}
            <p className="muted">Disimpulkan — menunggu konfirmasi.</p>
          </div>
        ))
      )}

      <h2>Ekspor dan hapus</h2>
      <p className="muted">
        Ekspor penuh dan hapus akun tersedia lewat jalur Fase 1 (puspa.consent.account). Hapus akun
        menghapus memori, alur, modul, kaset, dan blob; rantai hash Buku Kerja tetap utuh lewat
        tombstone. Tombol ekspor/hapus di PWA adalah follow-up setelah endpoint-nya mendarat.
      </p>
    </div>
  );
}
