// Renders emoji into 256px PNG icons for the workflow: swift make_icons.swift <outdir>
import AppKit

let icons: [String: String] = [
    "icon": "🍺", "formula": "📦", "cask": "🖥️", "installed": "✅", "outdated": "⬆️",
    "pinned": "📌", "vuln": "🛡️", "doctor": "🩺", "services": "⚙️", "running": "🟢",
    "stopped": "⚪️", "error": "🔴", "cleanup": "🧹", "cache": "🗑️", "preview": "🔍",
    "warning": "⚠️", "fix": "🔧", "update": "🔄", "search": "🔎", "link": "🔗",
    "uninstall": "❌", "appstore": "🛍️", "paid": "💳", "info": "ℹ️", "ok": "👍", "loading": "⏳", "terminal": "⌨️",
]
let out = CommandLine.arguments[1]
let size: CGFloat = 256
for (name, emoji) in icons {
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size), pixelsHigh: Int(size),
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { continue }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let str = NSAttributedString(string: emoji, attributes: [.font: NSFont.systemFont(ofSize: size * 0.8)])
    let b = str.size()
    str.draw(at: NSPoint(x: (size - b.width) / 2, y: (size - b.height) / 2))
    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(out)/\(name).png"))
}
