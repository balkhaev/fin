import type { Metadata } from "next"
import "./globals.css"
import { Roboto } from "next/font/google"

export const metadata: Metadata = {
  title: "BALKHAEV",
  description: "My giga app",
}

const roboto = Roboto({
  weight: ["400", "700"],
  style: ["normal", "italic"],
  subsets: ["latin"],
  display: "swap",
})

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en">
      <body className={`${roboto.className} antialiased`}>
        <div className="p-4 gap-4 h-screen overflow-hidden flex flex-col">
          {children}
        </div>
      </body>
    </html>
  )
}
