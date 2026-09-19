require 'net/http'

class Quotes
  def fetch(url)
    tries = 0
    begin
      Net::HTTP.get(URI(url))
    rescue StandardError
      retry if (tries += 1) < 3
      nil
    end
  end

  def fetch_forever(url)
    Net::HTTP.get(URI(url))
  rescue StandardError
    sleep 1
    retry
  end
end
