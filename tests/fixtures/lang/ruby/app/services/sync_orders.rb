require 'net/http'

class SyncOrders
  def call(ids)
    ids.each do |id|
      order = Order.find(id)
      push(order)
    end
  end

  def push(order)
    Net::HTTP.post(URI("https://x"), order.to_json)
  end
end
