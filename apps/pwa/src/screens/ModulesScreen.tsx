import { useState } from "react";
import { BUILTIN_MODULES, SKILLS_NOTE } from "../state/modules";

export function ModulesScreen() {
  const [openId, setOpenId] = useState<string | null>(null);
  const open = BUILTIN_MODULES.find((m) => m.id === openId);

  return (
    <div>
      <h1>Modul</h1>
      <p className="muted">
        Daftar modul dan alur: 2 modul bawaan dan yang dibuat user. Modul kustom terbuka sebagai
        panel di layar ini — modul tidak menambah layar.
      </p>
      {BUILTIN_MODULES.map((m) => (
        <div key={m.id} className="card">
          <strong>{m.name}</strong>
          <p>{m.description}</p>
          <span className="badge">Tersedia</span>{" "}
          <button type="button" onClick={() => setOpenId(openId === m.id ? null : m.id)}>
            {openId === m.id ? "Tutup pratinjau" : "Pratinjau"}
          </button>{" "}
          <a href="/chat">Buka di Chat</a>
        </div>
      ))}
      {open ? (
        <section className="card panel" aria-label={`Pratinjau ${open.name}`}>
          <h2>Pratinjau: {open.name}</h2>
          <p>{open.description}</p>
          <p className="muted">
            Modul berjalan lewat chat: minta Puspa memakai modul ini, dan tiap angka hasilnya
            membawa tombol Bukti. Pembayaran dan tindakan keluar selalu menunggu persetujuanmu di
            layar Tugas.
          </p>
          <a href="/chat">Pakai di Chat</a>
        </section>
      ) : null}

      <h2>Modul buatan user</h2>
      <p className="muted">
        Modul dibuat lewat chat, tanpa kode, dari blok yang sudah bersertifikat. Ceritakan ke Puspa
        alur yang kamu mau di Chat, dan modulmu akan muncul di sini setelah registry modul mendarat.
      </p>

      <h2>Skill</h2>
      <p className="muted">{SKILLS_NOTE}</p>
    </div>
  );
}
