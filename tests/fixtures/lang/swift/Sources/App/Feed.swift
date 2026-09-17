import Foundation

class Feed {
    let session = URLSession.shared

    func load(url: URL) throws -> Data {
        return try Data(contentsOf: url)
    }

    func refresh(urls: [URL]) async throws {
        for u in urls {
            let (_, _) = try await session.data(from: u)
        }
        _ = try load(url: urls[0])
        Thread.sleep(forTimeInterval: 1)
    }
}
