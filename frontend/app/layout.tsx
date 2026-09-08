import "./globals.css";

export const metadata = {
  title: "hookcut",
  description: "Turn your song or podcast into scroll-stopping short-form clips",
  icons: {
    icon: "/favicon.png",
    apple: "/icon-192.png",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
