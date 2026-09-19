import Foundation

class Quotes {
    let session = URLSession.shared

    func fetch(url: URL) async -> Data? {
        for attempt in 0..<3 {
            do {
                let (data, _) = try await session.data(from: url)
                return data
            } catch {
                continue
            }
        }
        return nil
    }
}
