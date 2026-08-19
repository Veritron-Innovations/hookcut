export const metadata = {
  title: "hookcut",
  description: "Turn your song or podcast into short-form clips",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", background: "#0e0e12", color: "#f2f2f5" }}>
        {children}
      </body>
    </html>
  );
}
