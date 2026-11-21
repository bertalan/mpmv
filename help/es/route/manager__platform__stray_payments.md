# Pagos huérfanos
last-checked: 2026-11-21

Esta página le muestra los pagos que se completaron pero no se usaron para un pedido, y le permite cerrar sus alertas cuando estén resueltas. Soporte y el propietario de la plataforma cierran las alertas; Finanzas puede leer la lista.

## La lista
Cada fila muestra la referencia del pago, la referencia del pedido, por qué el pago es huérfano (el pedido ya no espera el pago, el importe o la moneda no coinciden, la referencia del pago es errónea o ningún pedido coincide), el importe, la antigüedad en días y si el proveedor de pagos confirma un reembolso completo.

## El estado del reembolso
La página no consulta al proveedor de pagos. Muestra la última comprobación: el trabajo nocturno comprueba cada alerta desde el día 14, y Aún no comprobado significa que no hay ninguna comprobación registrada. Para comprobar una alerta ahora, elija Comprobar ahora en su fila: se consulta al proveedor una vez y el resultado se registra con su hora.

## Cerrar una alerta
1. Reembolse el pago a mano en el proveedor de pagos.
2. En su fila, escriba el motivo del cierre (por ejemplo, cómo se reembolsó) y elija Cerrar la alerta.

La alerta sale de la lista y el recordatorio diario se detiene. El registro de auditoría guarda solo una huella del motivo, así que no escriba datos personales en él. Una alerta ya cerrada sigue cerrada.

## Conviene saber
El trabajo nocturno cierra una alerta abierta desde hace 14 días cuando el proveedor confirma un reembolso completo; hasta entonces, Soporte recibe un recordatorio al día.
