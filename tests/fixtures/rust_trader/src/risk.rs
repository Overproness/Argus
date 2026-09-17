pub struct Node {
    pub value: f64,
    pub children: Vec<Node>,
}

pub fn exposure(node: &Node) -> f64 {
    let mut total = node.value;
    for c in &node.children {
        total += exposure(c);
    }
    total
}

pub fn correlation_matrix(prices: &[Vec<f64>]) -> Vec<Vec<f64>> {
    let mut out = vec![];
    for a in prices {
        let mut row = vec![];
        for b in prices {
            row.push(a.iter().zip(b).map(|(x, y)| x * y).sum());
        }
        out.push(row);
    }
    out
}
