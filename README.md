# Intoduction
Project to measue race time on a track. There can be multiple racers be on the track but they are not allowed to overtake.
The time measuremtent is done witm microcontrollers pico(2) W (Wifi connection to database via php script)
There is a disqualification page for racers that will not get to finish. 

# Time measurement
The micro controllers at least a StartGate and a finishingGate relly on a laser light barrier to measuere the time.
The Time is synched with a time servers so it is quite accurate, it`s resolution is milliseconds. However that does need internet connectivity. 

# Participans registration 
There is a race participant PHP script to register a participant online. 
This information can be used to create a participant for a race. Every participant need a RFID tag that will be used to start the race. 
To do the administration for the race there is a shiny application to handel all user comunication. 

# RFID 
The time measurement needs to be started by the start gate. 
To find the participant it use a RFID scanner to find the correct participant to be able to start the race. 



