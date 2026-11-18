import { useState } from "react";
import { subscribePush } from "../api/client";

/** Base64url -> Uint8Array for the VAPID applicationServerKey. */
function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const decoded = atob(base64.replace(/-/g, "+").replace(/_/g, "/") + padding);
  const bytes = new Uint8Array(decoded.length);
  for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
  return bytes;
}

export function SettingsScreen() {
  const [pushStatus, setPushStatus] = useState("");
  const [pushBusy, setPushBusy] = useState(false);

  async function enableNotifications() {
    setPushBusy(true);
    setPushStatus("");
    try {
      if (!("Notification" in window) || !("serviceWorker" in navigator)) {
        setPushStatus(
          "Peringatan: notifikasi tidak bisa menjangkau kamu di perangkat ini.",
        );
        return;
      }
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setPushStatus(
          "Peringatan: izin notifikasi tidak diberikan; pengingat hanya tampil di aplikasi.",
        );
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const vapidKey = import.meta.env.VITE_VAPID_PUBLIC_KEY as string | undefined;
      if (!vapidKey) {
        setPushStatus(
          "Notifikasi diizinkan di perangkat ini, tapi kunci VAPID belum dikonfigurasi di server (VITE_VAPID_PUBLIC_KEY kosong), jadi langganan push belum disimpan.",
        );
        return;
      }
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      });
      const payload = subscription.toJSON();
      await subscribePush({
        endpoint: payload.endpoint ?? "",
        keys: {
          p256dh: payload.keys?.p256dh ?? "",
          auth: payload.keys?.auth ?? "",
        },
      });
      setPushStatus("Notifikasi diizinkan dan langganan push tersimpan di server.");
    } catch (err) {
      setPushStatus(
        `Gagal mengaktifkan notifikasi: ${err instanceof Error ? err.message : "error tak dikenal"}`,
      );
    } finally {
      setPushBusy(false);
    }
  }

  return (
    <div>
      <h1>Pengaturan</h1>
      <div className="card">
        <strong>Batas belanja</strong>
        <p>Default Rp0 tanpa persetujuan. Agen tidak bisa menaikkannya sendiri.</p>
      </div>
      <div className="card">
        <strong>Bahasa</strong>
        <p>Indonesia.</p>
      </div>
      <div className="card">
        <strong>Notifikasi</strong>
        <p>
          Web Push (VAPID). Di iOS, push hanya jalan kalau PWA dipasang ke layar utama; onboarding
          menguji kirim dan menampilkan peringatan kalau notif tidak bisa menjangkau kamu.
        </p>
        <button type="button" onClick={enableNotifications} disabled={pushBusy}>
          {pushBusy ? "Mengaktifkan…" : "Izinkan notifikasi + uji kirim"}
        </button>
        {pushStatus ? <p className="muted">{pushStatus}</p> : null}
      </div>
      <div className="card">
        <strong>Akun dan perangkat</strong>
        <p>Daftar sesi dan perangkat bisa dilihat dan dicabut (sesi Fase 0).</p>
      </div>
      <div className="card">
        <strong>Persetujuan data</strong>
        <p>
          Teks v1-2026-11-17 (Fase 1). Persetujuan dicatat di Buku Kerja. Sebelum akses apa pun,
          kamu membaca dan menyetujui satu layar pendek: data apa yang disimpan, di mana, berapa
          lama, siapa yang bisa melihat, cara ekspor dan hapus.
        </p>
      </div>
      <div className="card">
        <strong>Rekor Otonomi</strong>
        <p>
          Di MVP semua aksi tetap T0 (tanya setiap kali). Rekor dikumpulkan; usulan naik ke T2
          untuk aksi nol-risiko muncul setelah 10 persetujuan berturut-turut tanpa edit.
        </p>
      </div>
    </div>
  );
}
