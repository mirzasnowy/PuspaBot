import { useLanguage } from "../state/language";

export function LoginScreen() {
  const { t } = useLanguage();
  return (
    <div>
      <h1>{t("ui.login.title", "Masuk ke Puspa")}</h1>
      <p>Login lewat akun Google (tautan email sebagai cadangan).</p>
      <a href="/auth/google">
        <button type="button">Masuk dengan Google</button>
      </a>
      <p className="muted">
        Kalau login Google belum dikonfigurasi di server ini, server akan menjawab bahwa
        PUSPA_GOOGLE_CLIENT_ID kosong — atur kredensial Google OAuth dulu.
      </p>
    </div>
  );
}
