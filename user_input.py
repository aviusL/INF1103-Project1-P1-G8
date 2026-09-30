from call_openai import ask_ai


print("Inser input here: ")


def initial_inputs():
    age = input("Age: ")
    gender = input("Gender: ")
    bmi = input("BMI: ")
    fitness = input("Fitness level: ")

    training_goal = input("Training goal: ")

    health_injury_history = input("Health/injury history: ")

    avail_equipment = input("Available equipment: ")

    avail_days = input("Available days and times: ")

    return age,gender,bmi,fitness,training_goal,health_injury_history,avail_equipment,avail_days



# GET THE INPUTS ----------------------
(
    age,
    gender,
    bmi,
    fitness,
    training_goal,
    health_injury_history,
    avail_equipment,
    avail_days
) = initial_inputs()



prompt = f"""
You are a fitness planning assistant.

Create a safe and realistic training plan based ONLY on the user's information below.

INITIAL USER INFORMATION
---------------
Age: {age}
Gender: {gender}
BMI: {bmi}
Fitness level: {fitness}

GOAL
----
{training_goal}

HEALTH AND INJURY HISTORY
-------------------------
{health_injury_history}

AVAILABLE EQUIPMENT
-------------------
{avail_equipment}

AVAILABILITY
------------
{avail_days}

INSTRUCTIONS
------------
1. Create a training plan that fits ONLY within the user's stated available days and times.
2. Respect the user's available equipment. Do not require equipment that is not available.
3. Take the user's injury history and current comments into account.
4. Do not prescribe exercises that could unnecessarily aggravate the reported injury or pain.
5. If an exercise may be unsuitable because of the user's injury history or current pain, replace it with a safer alternative.
6. The user's primary goal is the main training objective. Structure the plan around progressing toward that goal.
7. Do not assume the user has additional training time outside the stated availability.
8. Include rest or recovery when appropriate.
9. Keep the plan realistic for the user's current fitness level.
10. Do not diagnose injuries or medical conditions. If the reported pain could make training unsafe, clearly recommend seeking advice from a qualified healthcare professional.

OUTPUT FORMAT
-------------
For each training session, provide:

Day:
Time:
Workout:
Duration:
Intensity:
Purpose:
Equipment:
Safety notes:

After the sessions, provide:
- Weekly goal
- Total planned training time
- Important safety considerations

Keep the response concise and practical.
"""

result = ask_ai(prompt)

print(result)