use tokio::runtime::Handle;

pub async fn refresh(fail: bool) -> Result<(), String> {
    let handle = Handle::current();
    let task = handle.spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        if fail { Err("refresh unavailable".to_owned()) } else { Ok(()) }
    });
    task.await.map_err(|e| e.to_string())?
}
